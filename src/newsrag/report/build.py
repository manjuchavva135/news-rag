"""`newsrag report build` (ticket 030): the phase-1 quality report.

Writes `<quality_report_dir>/index.html` (self-contained, no external request), `report.json`
with the same numbers and `inputs/bar-<n>.json`, the queried numbers behind bars 1-3. Everything
is read again on every build; nothing it reads is changed.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

from newsrag import config
from newsrag.report import bars as B
from newsrag.report import collect
from newsrag.stages.base import write_json_atomic

TEMPLATE = Path(__file__).with_name("template.html")
NOT_AVAILABLE = "not available"

# (title, collect function). Titles are exact (FR-3).
SECTIONS: tuple[tuple[str, str], ...] = (
    ("Corpus", "corpus"),
    ("Dedup", "dedup"),
    ("Continuations", "continuations"),
    ("Low confidence", "low_confidence"),
    ("Editions", "editions"),
    ("Aliases", "aliases"),
    ("Bake-offs", "bakeoffs"),
    ("OCR fallback", "ocr_fallback"),
    ("Runs", "runs"),
)

# Bars whose numbers are queried (ledger, stores): (number, collect function, bar function).
QUERIED_BARS = (
    (1, "pages_input", B.pages_bar),
    (2, "indexed_input", B.indexed_bar),
    (3, "grouping_input", B.grouping_bar),
)

_SCHEME = re.compile(r"(?i)\bhttps?://")


@dataclass
class Report:
    phase1: str
    header: str
    bars: list[B.Bar]
    sections: dict[str, Any]  # title -> numbers, or None (not available)
    errors: dict[str, str]  # title or `bar-<n>` -> the error that made it unavailable
    generated_at: str

    def as_json(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "phase1": self.phase1,
            "header": self.header,
            "bars": [b.as_json() for b in self.bars],
            "sections": self.sections,
            "errors": self.errors,
        }


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".splitlines()[0][:300]


def _link(path: Path, out_dir: Path) -> str:
    return Path(os.path.relpath(Path(path).resolve(), out_dir.resolve())).as_posix()


def collect_bars(out_dir: Path, now: str) -> tuple[list[B.Bar], dict[str, str]]:
    s = config.settings
    out, errors = [], {}
    for number, fn_name, judge in QUERIED_BARS:
        data, error = None, None
        try:
            data = getattr(collect, fn_name)()
        except Exception as exc:  # noqa: BLE001 - an unreadable input is a FAIL (D-2)
            error = _error(exc)
            errors[f"bar-{number}"] = error
        snapshot = out_dir / "inputs" / f"bar-{number}.json"
        write_json_atomic(
            snapshot, {"bar": number, "generated_at": now, "data": data, "error": error}
        )
        bar = judge(data)
        if error:
            bar.note = error
        bar.source = f"inputs/bar-{number}.json"
        out.append(bar)

    spot = Path(s.transcribe_spotcheck_file)
    verdicts = collect.ocr_verdicts_file()
    compare = Path(s.baseline_compare_file)
    answers = Path(s.customer_answers_file)
    for bar, path in (
        (B.transcription_bar(collect.read_json(spot)), spot),
        (B.ocr_signoff_bar(collect.read_json(verdicts)), verdicts),
        (B.retrieval_bar(collect.read_json(compare)), compare),
        (
            B.customer_bar(
                collect.read_json(answers),
                collect.read_json(Path(s.customer_signoff_file)),
                collect.file_sha1(answers),
            ),
            answers,
        ),
    ):
        bar.source = _link(path, out_dir)
        out.append(bar)
    return out, errors


def collect_sections() -> tuple[dict[str, Any], dict[str, str]]:
    out, errors = {}, {}
    for title, fn_name in SECTIONS:
        try:
            out[title] = getattr(collect, fn_name)()
        except Exception as exc:  # noqa: BLE001 - a missing input shows "not available"
            out[title] = None
            errors[title] = _error(exc)
    return out, errors


def build(out_dir: Path | None = None) -> tuple[Report, Path]:
    """Collect everything, write the snapshots, report.json and index.html; return the report and
    the page's path."""
    out_dir = Path(out_dir or config.settings.quality_report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars, bar_errors = collect_bars(out_dir, now)
    sections, section_errors = collect_sections()
    phase1, header = B.verdict(bars)
    report = Report(phase1, header, bars, sections, {**bar_errors, **section_errors}, now)
    write_json_atomic(out_dir / "report.json", report.as_json())
    page = out_dir / "index.html"
    tmp = page.with_suffix(".html.tmp")
    tmp.write_text(render(report), encoding="utf-8")
    tmp.replace(page)
    return report, page


# --- HTML -----------------------------------------------------------------------------------------


def esc(value: Any) -> str:
    """Escaped text; a URL scheme is dropped so the page never points outside (FR-1)."""
    return html.escape(_SCHEME.sub("", str(value)))


def fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(fmt(v) for v in value) if value else "-"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {fmt(v)}" for k, v in value.items()) or "-"
    return str(value)


def _cell(value: Any) -> str:
    cls = ' class="num"' if isinstance(value, (int, float)) and not isinstance(value, bool) else ""
    return f"<td{cls}>{esc(fmt(value))}</td>"


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, dict) and not (
        isinstance(value, list) and value and isinstance(value[0], dict)
    )


def _row_table(rows: list[dict[str, Any]]) -> str:
    columns: list[str] = []
    for row in rows:
        columns += [k for k in row if k not in columns]
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(_cell(r.get(c)) for c in columns) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_value(value: Any, key_name: str = "") -> str:
    """A dict of scalars as a two-column table, a dict of flat dicts (e.g. per year) or a list of
    flat dicts as one table, deeper values under their own sub-heading."""
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        return _row_table(value)
    if isinstance(value, dict):
        if value and all(
            isinstance(v, dict) and all(_is_scalar(x) for x in v.values()) for v in value.values()
        ):
            first = key_name or ("year" if all(str(k).isdigit() for k in value) else "name")
            return _row_table([{first: k, **v} for k, v in value.items()])
        scalars = {k: v for k, v in value.items() if _is_scalar(v)}
        nested = {k: v for k, v in value.items() if not _is_scalar(v)}
        parts = []
        if scalars:
            rows = "".join(f"<tr><th>{esc(k)}</th>{_cell(v)}</tr>" for k, v in scalars.items())
            parts.append(f"<table><tbody>{rows}</tbody></table>")
        for k, v in nested.items():
            parts.append(f"<h3>{esc(k)}</h3>{render_value(v, 'year' if k == 'per_year' else '')}")
        return "".join(parts) or f'<p class="na">{NOT_AVAILABLE}</p>'
    return f"<p>{esc(fmt(value))}</p>"


def render_bakeoffs(data: dict[str, Any]) -> str:
    parts = []
    for name, entry in data.items():
        parts.append(f"<h3>{esc(name)}</h3>")
        if entry is None:
            parts.append(f'<p class="na">{NOT_AVAILABLE}</p>')
            continue
        winners = ", ".join(entry["winner"]) or "none"
        parts.append(f"<p>winner: <strong>{esc(winners)}</strong></p>")
        # The opening and closing tags sit on lines of their own (eval E-4 strips these blocks).
        parts.append(f'\n<pre class="bakeoff">\n{html.escape(entry["text"])}\n</pre>\n')
    return "".join(parts)


def render_bars(bars: list[B.Bar]) -> str:
    rows = []
    for b in bars:
        status = "PASS" if b.passed else "FAIL"
        name = esc(f"{b.number}. {b.name}")
        if b.source:
            name = f'<a href="{html.escape(b.source)}">{name}</a>'
        note = f'<span class="note">{esc(b.note)}</span>' if b.note else ""
        rows.append(
            f'<tr><td>{name}{note}</td><td data-label="value">{esc(b.value)}</td>'
            f'<td data-label="threshold">{esc(b.threshold)}</td>'
            f'<td><span class="status {status}">{status}</span></td></tr>'
        )
    head = "<tr><th>Bar</th><th>Value</th><th>Threshold</th><th>Result</th></tr>"
    return f'<table class="bars"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def render_sections(report: Report) -> str:
    parts = []
    for title, _fn in SECTIONS:
        data = report.sections.get(title)
        parts.append(f"<h2>{esc(title)}</h2>")
        if data is None:
            why = report.errors.get(title)
            note = f'<span class="note">{esc(why)}</span>' if why else ""
            parts.append(f'<p class="na">{NOT_AVAILABLE}{note}</p>')
        elif title == "Bake-offs":
            parts.append(render_bakeoffs(data))
        else:
            parts.append(render_value(data))
    return "\n".join(parts)


def render(report: Report) -> str:
    return Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        phase=report.phase1,
        header=esc(report.header),
        generated_at=esc(report.generated_at),
        bars=render_bars(report.bars),
        sections=render_sections(report),
    )
