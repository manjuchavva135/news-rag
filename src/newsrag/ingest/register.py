"""Register one ledger row per unique page (ticket 004, FR-3 ... FR-7).

Every stem under ``source_dir`` with a valid ``.unpacked`` marker is scanned, all of them together:
a page may appear in two archives. Winners go to ``pages``, unparseable files to ``quarantine``,
and one ``register-<stem>.json`` report is written per scanned stem.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from newsrag.config import settings
from newsrag.ingest.filenames import PARSE_REASONS, ParsedPage, Rejected, parse
from newsrag.ingest.unpack import marker_path, read_marker
from newsrag.ledger import db
from newsrag.stages.base import write_json_atomic

SOURCE_MISSING = "source_missing"

Echo = Callable[[str], None]


@dataclass
class StemScan:
    stem: str
    pdfs: list[str] = field(default_factory=list)
    parsed: list[ParsedPage] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)


def find_stems(source_dir: Path, echo: Echo) -> tuple[list[str], list[str]]:
    """(stems with a valid marker, stems skipped)."""
    scanned, skipped = [], []
    if not source_dir.is_dir():
        return scanned, skipped
    for entry in sorted(source_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if read_marker(marker_path(source_dir, entry.name)) is None:
            echo(f"skipping {entry.name}: not fully unpacked")
            skipped.append(entry.name)
        else:
            scanned.append(entry.name)
    return scanned, skipped


def scan_stem(source_dir: Path, stem: str) -> StemScan:
    scan = StemScan(stem)
    for root, dirs, files in os.walk(source_dir / stem):
        dirs.sort()
        for name in sorted(files):
            if not name.lower().endswith(".pdf"):
                continue
            path = os.path.join(root, name)
            scan.pdfs.append(path)
            got = parse(path)
            if isinstance(got, ParsedPage):
                scan.parsed.append(got)
            else:
                scan.rejected.append(got)
    return scan


def winner_key(page: ParsedPage, source_dir: Path) -> tuple:
    """Sort key: highest revision, then print copy, then a path in /out/, then the path."""
    return (
        -page.revision,
        page.copy_kind != "print",
        "/out/" not in page.path,
        os.path.relpath(page.path, source_dir),
    )


def dedup(pages: list[ParsedPage], source_dir: Path) -> tuple[dict[str, ParsedPage], list]:
    """(winner per page_id, [(loser, winner)])."""
    groups: dict[tuple, list[ParsedPage]] = {}
    for page in pages:
        groups.setdefault(page.group_key, []).append(page)
    winners: dict[str, ParsedPage] = {}
    losers: list[tuple[ParsedPage, ParsedPage]] = []
    for group in groups.values():
        group.sort(key=lambda p: winner_key(p, source_dir))
        winner = group[0]
        winners[winner.page_id] = winner
        losers.extend((loser, winner) for loser in group[1:])
    return winners, losers


def _page_values(page: ParsedPage, stem: str) -> dict[str, Any]:
    return {
        "page_id": page.page_id,
        "pub_date": page.date,
        "edition_code": page.edition_code,
        "section": page.section,
        "page_num": page.page,
        "page_end": page.page_end,
        "source_path": page.path,
        "source_archive": stem,
        "revision": page.revision,
    }


def write_ledger(
    winners: dict[str, ParsedPage],
    stem_of: dict[str, str],
    rejected: list[tuple[Rejected, str]],
    scanned: list[str],
    skipped: list[str],
) -> dict[str, int]:
    """Apply one register run to the ledger in a single transaction. Returns change counts."""
    counts = {"inserted": 0, "updated": 0, "restored": 0, "source_missing": 0}
    with db.conn() as c:
        c.execute(
            "select pg_advisory_xact_lock(hashtext(%s))", (f"newsrag-register:{db.schema()}",)
        )
        existing = {
            row["page_id"]: row
            for row in db.fetch_all(
                c, "select page_id, source_path, source_archive, quarantine_reason from pages"
            )
        }
        inserts, updates, restores = [], [], []
        for pid, page in winners.items():
            row = existing.get(pid)
            values = _page_values(page, stem_of[page.path])
            if row is None:
                inserts.append(values)
            elif row["source_archive"] in skipped:
                continue  # its stem was not scanned this run: leave it alone
            elif row["source_path"] != page.path:
                updates.append({**values, "old_path": row["source_path"]})
            elif row["quarantine_reason"] == SOURCE_MISSING:
                restores.append(values)

        with c.cursor() as cur:
            if inserts:
                cur.executemany(
                    "insert into pages (page_id, pub_date, edition_code, section, page_num, "
                    "page_end, source_path, source_archive, revision) values (%(page_id)s, "
                    "%(pub_date)s, %(edition_code)s, %(section)s, %(page_num)s, %(page_end)s, "
                    "%(source_path)s, %(source_archive)s, %(revision)s)",
                    inserts,
                )
            if updates:
                # A different winner (a newer revision arrived): reprocess from the start.
                cur.executemany(
                    "update pages set source_path = %(source_path)s, "
                    "source_archive = %(source_archive)s, revision = %(revision)s, "
                    "page_end = %(page_end)s, quarantine_reason = null, updated_at = now() "
                    "where page_id = %(page_id)s",
                    updates,
                )
                cur.executemany("delete from quarantine where source_path = %(old_path)s", updates)
                cur.executemany("delete from stage_runs where page_id = %(page_id)s", updates)
            if restores:
                cur.executemany(
                    "update pages set quarantine_reason = null, updated_at = now() "
                    "where page_id = %(page_id)s",
                    restores,
                )
                cur.executemany(
                    "delete from quarantine where source_path = %(source_path)s and reason = %(r)s",
                    [{**v, "r": SOURCE_MISSING} for v in restores],
                )

            missing = [
                {"page_id": pid, "source_path": row["source_path"], "stem": row["source_archive"]}
                for pid, row in existing.items()
                if row["source_archive"] in scanned
                and pid not in winners
                and row["quarantine_reason"] != SOURCE_MISSING
                and not os.path.exists(row["source_path"])
            ]
            if missing:
                cur.executemany(
                    "update pages set quarantine_reason = %(r)s, updated_at = now() "
                    "where page_id = %(page_id)s",
                    [{**m, "r": SOURCE_MISSING} for m in missing],
                )
                cur.executemany(
                    "insert into quarantine (source_path, source_archive, reason) "
                    "values (%(source_path)s, %(stem)s, %(r)s) on conflict (source_path) do "
                    "update set source_archive = excluded.source_archive, "
                    "reason = excluded.reason, detected_at = now()",
                    [{**m, "r": SOURCE_MISSING} for m in missing],
                )

            if rejected:
                cur.executemany(
                    "insert into quarantine (source_path, source_archive, reason) "
                    "values (%(path)s, %(stem)s, %(reason)s) on conflict (source_path) do "
                    "update set source_archive = excluded.source_archive, "
                    "reason = excluded.reason, detected_at = now() "
                    "where quarantine.reason is distinct from excluded.reason "
                    "or quarantine.source_archive is distinct from excluded.source_archive",
                    [{"path": r.path, "stem": stem, "reason": r.reason} for r, stem in rejected],
                )
            # Parse-reason rows of scanned stems whose file now parses (or is gone).
            cur.execute(
                "delete from quarantine where reason = any(%s) and source_archive = any(%s) "
                "and not (source_path = any(%s))",
                (list(PARSE_REASONS), scanned, [r.path for r, _ in rejected]),
            )
        counts.update(
            inserted=len(inserts),
            updated=len(updates),
            restored=len(restores),
            source_missing=len(missing),
        )
    return counts


def build_report(scan: StemScan, winners: dict[str, ParsedPage], losers: list) -> dict[str, Any]:
    in_stem = set(scan.pdfs)
    registered = sum(1 for w in winners.values() if w.path in in_stem)
    superseded = [
        {"path": loser.path, "winner": winner.path}
        for loser, winner in losers
        if loser.path in in_stem
    ]
    quarantined = [{"path": r.path, "reason": r.reason} for r in scan.rejected]
    denominator = registered + len(quarantined)
    pct = round(len(quarantined) / denominator * 100, 2) if denominator else 0.0
    return {
        "stem": scan.stem,
        "source_pdfs": len(scan.pdfs),
        "registered": registered,
        "superseded": superseded,
        "quarantined": quarantined,
        "day_mismatch_warnings": sum(1 for p in scan.parsed if p.day_mismatch),
        "quarantine_pct": pct,
    }


def register_all(
    *,
    source_dir: Path | None = None,
    reports_dir: Path | None = None,
    echo: Echo = print,
) -> dict[str, dict[str, Any]]:
    """Scan every unpacked stem, update the ledger and write the reports. Returns the reports."""
    source_dir = Path(source_dir or settings.source_dir).absolute()
    reports_dir = Path(reports_dir or settings.reports_dir)
    scanned, skipped = find_stems(source_dir, echo)
    scans = [scan_stem(source_dir, stem) for stem in scanned]

    stem_of = {path: s.stem for s in scans for path in s.pdfs}
    winners, losers = dedup([p for s in scans for p in s.parsed], source_dir)
    rejected = [(r, s.stem) for s in scans for r in s.rejected]
    counts = write_ledger(winners, stem_of, rejected, scanned, skipped)

    reports: dict[str, dict[str, Any]] = {}
    for scan in scans:
        report = build_report(scan, winners, losers)
        write_json_atomic(reports_dir / f"register-{scan.stem}.json", report)
        reports[scan.stem] = report
        echo(
            f"{scan.stem}: pdfs={report['source_pdfs']} registered={report['registered']} "
            f"superseded={len(report['superseded'])} quarantined={len(report['quarantined'])} "
            f"({report['quarantine_pct']:.2f}%)"
        )
    if not scans:
        echo(f"nothing to register: no unpacked archive under {source_dir}")
    else:
        echo(
            "ledger: {inserted} new, {updated} new winner, {restored} source back, "
            "{source_missing} source missing".format(**counts)
        )
    return reports
