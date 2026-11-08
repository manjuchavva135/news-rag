#!/usr/bin/env node
// Super Agent watchdog scan.
//
// Lists problems the router would otherwise never hear about:
//   went-quiet      a worker worktree with no file change for `stale_minutes`
//   awaiting-merge  a worker finished (clean branch + results.md) but nothing merged it
//   possible-loop   results.md shows the same check failing `loop_attempts` times
//   cycle           tickets on the board waiting on each other
//   blocked         a ready ticket waits on a draft ticket (or on a ticket that doesn't exist)
//   board-mismatch  BOARD.md and rd.md disagree on a ticket's status
//   main-app-down   nothing answers on base_port while work is in progress
//
// Usage (from the project root):
//   node specs/.super-agent/watchdog-scan.mjs --once           print problems once, exit
//   node specs/.super-agent/watchdog-scan.mjs --watch          loop; print only when problems change
//   node specs/.super-agent/watchdog-scan.mjs --once --json    machine-readable output
//   --root <dir>   project root (default: current directory)
//
// Rules are read from specs/.super-agent/watchdog-rules.md every round (key: value lines).
// No dependencies. Node 18+.

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : dflt;
};

const ROOT = path.resolve(opt("--root", process.cwd()));
const TICKETS = path.join(ROOT, "specs", "tickets");
const STATE_DIR = path.join(ROOT, "specs", ".super-agent");
const RULES_FILE = path.join(STATE_DIR, "watchdog-rules.md");
const LOG_FILE = path.join(STATE_DIR, "watchdog.log");
const SKIP_DIRS = new Set(["node_modules", ".git", ".next", "dist", "build", "out", ".turbo", ".cache", "coverage", "evidence"]);

const DEFAULT_RULES = { interval_seconds: 600, stale_minutes: 20, loop_attempts: 3, base_port: 3000, check_main_app: true, ignore: [] };

function readRules() {
  const rules = { ...DEFAULT_RULES, ignore: [] };
  let text = "";
  try { text = fs.readFileSync(RULES_FILE, "utf8"); } catch { return rules; }
  for (const raw of text.split(/\r?\n/)) {
    const m = raw.match(/^\s*([a-z_]+)\s*:\s*(.*)$/i);
    if (!m) continue;
    const key = m[1].toLowerCase();
    const val = m[2].trim();
    if (key === "ignore") {
      rules.ignore = val.split(/[,\s]+/).filter(Boolean).map((s) => s.match(/\d{1,3}/)?.[0]?.padStart(3, "0")).filter(Boolean);
    } else if (key === "check_main_app") {
      rules.check_main_app = !/^(no|false|off|0)$/i.test(val);
    } else if (key in DEFAULT_RULES && val !== "" && !Number.isNaN(Number(val))) {
      rules[key] = Number(val);
    }
  }
  return rules;
}

function git(cwd, ...gitArgs) {
  try {
    return execFileSync("git", ["-C", cwd, ...gitArgs], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 15000 }).trim();
  } catch {
    return null;
  }
}

// ---------- board and tickets ----------

const pad = (n) => String(n).padStart(3, "0");
const normStatus = (s) => (s || "").toLowerCase().replace(/[*_`]/g, "").trim();

function parseBoard() {
  const file = path.join(TICKETS, "BOARD.md");
  let text;
  try { text = fs.readFileSync(file, "utf8"); } catch { return null; }
  const rows = new Map();
  let cols = null;
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim().startsWith("|")) continue;
    const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    if (cells.every((c) => /^:?-{2,}:?$/.test(c) || c === "")) continue;
    if (!cols) {
      const lower = cells.map((c) => c.toLowerCase());
      if (lower.includes("status")) cols = lower;
      continue;
    }
    const get = (name) => cells[cols.indexOf(name)] ?? "";
    const idMatch = (get("#") || cells[0]).match(/\d{1,3}/);
    if (!idMatch) continue;
    const id = pad(idMatch[0]);
    const runs = get("runs");
    const waits = /wait/i.test(runs) ? [...(runs.split(":")[0].matchAll(/\b(\d{3})\b/g))].map((m) => m[1]) : [];
    rows.set(id, { id, title: get("title"), status: normStatus(get("status")), mode: get("mode").toLowerCase(), runs, waits });
  }
  return rows;
}

function ticketFolders() {
  const out = new Map();
  let entries = [];
  try { entries = fs.readdirSync(TICKETS, { withFileTypes: true }); } catch { return out; }
  for (const e of entries) {
    const m = e.isDirectory() && e.name.match(/^(\d{3})-/);
    if (!m) continue;
    const dir = path.join(TICKETS, e.name);
    let status = null;
    try {
      const rd = fs.readFileSync(path.join(dir, "rd.md"), "utf8");
      status = normStatus(rd.match(/\*\*Status:\*\*\s*([A-Za-z-]+)/)?.[1]);
    } catch {}
    out.set(m[1], { id: m[1], name: e.name, dir, status });
  }
  return out;
}

function findCycles(board) {
  const cycles = [];
  const state = new Map(); // 1 = visiting, 2 = done
  const stack = [];
  const visit = (id) => {
    state.set(id, 1);
    stack.push(id);
    for (const dep of board.get(id)?.waits || []) {
      const d = board.get(dep);
      if (!d || d.status === "done") continue;
      if (state.get(dep) === 1) cycles.push(stack.slice(stack.indexOf(dep)).concat(dep));
      else if (!state.get(dep)) visit(dep);
    }
    stack.pop();
    state.set(id, 2);
  };
  for (const [id, t] of board) if (t.status !== "done" && !state.get(id)) visit(id);
  return cycles;
}

// ---------- results.md loop detection ----------

function loopCheck(resultsFile, limit) {
  let text;
  try { text = fs.readFileSync(resultsFile, "utf8"); } catch { return null; }
  const counts = new Map();
  for (const line of text.split(/\r?\n/)) {
    if (!/^\s*[-*]\s*attempt\s*\d+/i.test(line) || !/fail/i.test(line)) continue;
    for (const m of line.matchAll(/\b(E-\d+|build|lint|typecheck|tests?)\b/gi)) {
      const key = m[1].toUpperCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  for (const [check, n] of counts) if (n >= limit) return { check, n };
  return null;
}

// ---------- worktrees ----------

function newestMtime(dir, budget = { files: 20000 }) {
  let newest = 0;
  const walk = (d) => {
    let entries;
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      if (budget.files-- <= 0) return;
      if (e.name === ".git") continue; // in a worktree .git is a pointer file git rewrites itself
      if (e.isDirectory()) {
        if (!SKIP_DIRS.has(e.name)) walk(path.join(d, e.name));
      } else {
        try { newest = Math.max(newest, fs.statSync(path.join(d, e.name)).mtimeMs); } catch {}
      }
    }
  };
  walk(dir);
  return newest;
}

function worktrees() {
  const out = [];
  const porcelain = git(ROOT, "worktree", "list", "--porcelain");
  if (!porcelain) return out;
  let cur = null;
  for (const line of porcelain.split(/\r?\n/)) {
    if (line.startsWith("worktree ")) { cur = { path: path.resolve(line.slice(9)) }; out.push(cur); }
    else if (cur && line.startsWith("branch ")) cur.branch = line.slice(7).replace("refs/heads/", "");
  }
  const marker = `${path.sep}.claude${path.sep}worktrees${path.sep}`;
  return out.filter((w) => w.path.includes(marker));
}

function mainBranch() {
  try {
    const pm = fs.readFileSync(path.join(STATE_DIR, "project.md"), "utf8");
    const m = pm.match(/Main branch:\s*`?([\w./-]+)`?/i);
    if (m) return m[1];
  } catch {}
  for (const b of ["main", "master"]) if (git(ROOT, "rev-parse", "--verify", "--quiet", b)) return b;
  return git(ROOT, "branch", "--show-current") || "main";
}

// ---------- main app ----------

async function portAnswers(port) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 4000);
  try {
    await fetch(`http://127.0.0.1:${port}/`, { signal: ctrl.signal, redirect: "manual" });
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// ---------- scan ----------

async function scan() {
  const rules = readRules();
  const now = Date.now();
  const problems = [];
  const add = (kind, ticket, message) => {
    if (ticket && rules.ignore.includes(ticket)) return;
    problems.push({ kind, ticket: ticket || null, message, key: `${kind}:${ticket || "-"}` });
  };

  const board = parseBoard();
  const folders = ticketFolders();

  if (!board) {
    add("no-board", null, "specs/tickets/BOARD.md not found - run /super-agent:write-tickets or /super-agent:setup");
  } else {
    for (const cycle of findCycles(board)) add("cycle", cycle[0], `tickets wait on each other: ${cycle.join(" -> ")}`);
    for (const t of board.values()) {
      if (t.status !== "ready") continue;
      for (const dep of t.waits) {
        const d = board.get(dep);
        if (!d) add("blocked", t.id, `${t.id} waits for ${dep}, which is not on the board`);
        else if (d.status === "draft") add("blocked", t.id, `${t.id} is ready but waits for ${dep}, which is still a draft - it will never start`);
      }
    }
    for (const [id, f] of folders) {
      const b = board.get(id);
      if (!b) add("board-mismatch", id, `${f.name} has a folder but no row on BOARD.md`);
      else if (f.status && b.status && f.status !== b.status) add("board-mismatch", id, `BOARD.md says ${b.status}, rd.md says ${f.status}`);
    }
  }

  // Loops in the main checkout (team tickets write results here).
  for (const [id, f] of folders) {
    const loop = loopCheck(path.join(f.dir, "results.md"), rules.loop_attempts);
    if (loop) add("possible-loop", id, `${loop.check} failed ${loop.n} times in ${f.name}/results.md`);
  }

  // Worker / reproducer worktrees.
  const main = mainBranch();
  const wts = worktrees();
  const mergedTips = new Set(
    (git(ROOT, "rev-list", "--merges", "--parents", "-n", "300", main) || "")
      .split(/\r?\n/)
      .flatMap((l) => l.split(" ").slice(2)),
  );
  for (const w of wts) {
    const id = (w.branch || path.basename(w.path)).match(/(?:^|\D)(\d{3})(?:\D|$)/)?.[1] || null;
    const label = id ? id : w.branch || path.basename(w.path);
    if (id && (board?.get(id)?.status === "done")) continue;

    const dirty = (git(w.path, "status", "--porcelain") || "") !== "";
    // Already merged? The merger uses --no-ff, so a merged branch tip is a parent of a merge commit on main.
    const tip = git(w.path, "rev-parse", "HEAD");
    if (tip && !dirty && mergedTips.has(tip)) continue;
    const lastFile = newestMtime(w.path);
    const lastCommit = Number(git(w.path, "log", "-1", "--format=%ct") || 0) * 1000;
    const lastActivity = Math.max(lastFile, lastCommit);
    const idleMin = Math.round((now - lastActivity) / 60000);

    let ticketDir = null;
    if (id) {
      const f = folders.get(id);
      if (f) ticketDir = path.join(w.path, path.relative(ROOT, f.dir));
    }
    const hasResults = ticketDir ? fs.existsSync(path.join(ticketDir, "results.md")) : false;

    if (ticketDir) {
      const loop = loopCheck(path.join(ticketDir, "results.md"), rules.loop_attempts);
      if (loop) add("possible-loop", id, `${label}: ${loop.check} failed ${loop.n} times (worktree ${path.relative(ROOT, w.path)})`);
    }

    if (idleMin >= rules.stale_minutes) {
      if (!dirty && hasResults) add("awaiting-merge", id || label, `${label} finished ${idleMin} min ago (clean branch ${w.branch}, results.md written) but is not merged`);
      else add("went-quiet", id || label, `${label} went quiet: no file change for ${idleMin} min (branch ${w.branch || "?"}, ${dirty ? "uncommitted changes" : "nothing uncommitted"})`);
    }
  }

  // Main app: only meaningful while something is being built.
  const working = wts.length > 0 || (board && [...board.values()].some((t) => t.status === "ready"));
  if (rules.check_main_app && working && !(await portAnswers(rules.base_port))) {
    add("main-app-down", null, `nothing answers on :${rules.base_port} (main checkout dev server)`);
  }

  return { at: new Date(now).toISOString(), worktrees: wts.length, problems, rules };
}

function format(result) {
  if (!result.problems.length) return `watchdog: no problems (${result.worktrees} worktree(s) checked)`;
  return result.problems.map((p) => `WARN ${p.kind}${p.ticket ? " " + p.ticket : ""}: ${p.message}`).join("\n");
}

function log(result) {
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    const summary = result.problems.length ? result.problems.map((p) => p.key).join(", ") : "ok";
    fs.appendFileSync(LOG_FILE, `${result.at} worktrees=${result.worktrees} ${summary}\n`);
  } catch {}
}

async function main() {
  if (flag("--help") || flag("-h")) {
    console.log("usage: node watchdog-scan.mjs --once | --watch [--json] [--root <dir>]");
    return;
  }
  if (!flag("--watch")) {
    const result = await scan();
    log(result);
    console.log(flag("--json") ? JSON.stringify(result, null, 2) : format(result));
    return;
  }
  let lastKeys = null;
  for (;;) {
    const result = await scan();
    log(result);
    const keys = result.problems.map((p) => p.key).sort().join("|");
    if (keys !== lastKeys) {
      const stamp = new Date().toLocaleTimeString();
      if (result.problems.length) console.log(`[${stamp}] ${format(result).split("\n").join(`\n[${stamp}] `)}`);
      else if (lastKeys !== null) console.log(`[${stamp}] watchdog: all clear`);
      else console.log(`[${stamp}] watchdog: watching, no problems`);
      lastKeys = keys;
    }
    await new Promise((r) => setTimeout(r, Math.max(30, result.rules.interval_seconds) * 1000));
  }
}

main().catch((err) => {
  console.error(`watchdog-scan error: ${err?.message || err}`);
  process.exitCode = 1;
});
