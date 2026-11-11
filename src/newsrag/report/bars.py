"""The phase-1 pass bars (ticket 030 FR-2, grill D-14 / D-21 / A-34), as pure functions.

Each function takes the numbers or the parsed file it judges (None = the input is missing) and
returns a `Bar`. A missing input is a FAIL (D-2): nothing passes by default.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# --- thresholds ---------------------------------------------------------------------------------
MAX_QUARANTINE_PCT = 1.0  # bar 1: quarantined share of the files, at most
INDEXED_PCT = 100.0  # bar 2: share of the pages with their date indexed, exactly
MIN_JUDGE_MEAN = 0.90  # bar 3: mean kept judge score, at least
MAX_JUDGE_LOW_PCT = 5.0  # bar 3: pages flagged `low_confidence:judge_low`, at most
MAX_INVENTED_PCT = 2.0  # bar 4: transcribed lines not in the scan, at most
MIN_SUPPORTED_PCT = 90.0  # bar 7: cited claims the sources support, at least
TEST_SIGNER = "eval-tester"  # the tester's own sign-off name: never a real reading (bars 5, 7)

NAMES = (
    "Pages",
    "Indexed",
    "Grouping",
    "Transcription",
    "OCR sanity check",
    "Retrieval vs old",
    "Customer answers",
)


@dataclass
class Bar:
    number: int
    name: str
    value: str  # what was measured, as shown
    threshold: str
    passed: bool
    source: str | None = None  # link relative to the report page; set by the builder
    note: str = ""  # why it failed / what the value is made of

    def as_json(self) -> dict[str, Any]:
        return {**asdict(self), "status": "PASS" if self.passed else "FAIL"}


def _missing(number: int, threshold: str, note: str = "input missing") -> Bar:
    return Bar(number, NAMES[number - 1], "missing", threshold, False, note=note)


def _pct(value: float) -> str:
    return f"{value:.2f}%"


def quarantine_pct(rejects: int, quarantined_pages: int, registered: int) -> float | None:
    """Bar 1's formula: (filename rejects + quarantined pages) / (registered + rejects) x 100."""
    total = registered + rejects
    return (rejects + quarantined_pages) / total * 100 if total else None


def pages_bar(data: dict[str, Any] | None) -> Bar:
    """Bar 1. `data` = {rejects, quarantined_pages, registered}."""
    threshold = f"<= {MAX_QUARANTINE_PCT:g}%"
    if data is None:
        return _missing(1, threshold)
    pct = quarantine_pct(data["rejects"], data["quarantined_pages"], data["registered"])
    if pct is None:
        return _missing(1, threshold, "no page registered")
    note = (
        f"{data['rejects']} filename rejects + {data['quarantined_pages']} quarantined pages "
        f"of {data['registered']} registered + {data['rejects']} rejects"
    )
    return Bar(1, NAMES[0], _pct(pct), threshold, pct <= MAX_QUARANTINE_PCT, note=note)


def indexed_bar(data: dict[str, Any] | None) -> Bar:
    """Bar 2. `data` = {pages, indexed_pages, active_generation, expected_generation,
    validate_ok, validate_error?}."""
    threshold = f"= {INDEXED_PCT:g}% and index validate ok"
    if data is None:
        return _missing(2, threshold)
    if not data["pages"]:
        return _missing(2, threshold, "no non-quarantined page")
    pct = data["indexed_pages"] / data["pages"] * 100
    problems = []
    if data["indexed_pages"] != data["pages"]:
        problems.append(f"{data['pages'] - data['indexed_pages']} pages not indexed")
    if data["active_generation"] != data["expected_generation"]:
        problems.append(
            f"active generation {data['active_generation']} is not {data['expected_generation']}"
        )
    if not data["validate_ok"]:
        problems.append(f"index validate failed: {data.get('validate_error') or 'mismatch'}")
    note = "; ".join(problems) or (
        f"{data['indexed_pages']}/{data['pages']} pages, generation {data['active_generation']}"
    )
    return Bar(2, NAMES[1], _pct(pct), threshold, not problems, note=note)


def grouping_bar(data: dict[str, Any] | None) -> Bar:
    """Bar 3. `data` = `grouping.stats.summarize` over the current `group_final` pages."""
    threshold = f"mean >= {MIN_JUDGE_MEAN:.2f}, judge_low <= {MAX_JUDGE_LOW_PCT:g}%"
    if data is None or not data["pages"]:
        return _missing(3, threshold, "no current group_final page")
    mean, low = data["mean_score"], data["judge_low_pct"]
    if mean is None:
        return _missing(3, threshold, "no scored page")
    passed = mean >= MIN_JUDGE_MEAN and low <= MAX_JUDGE_LOW_PCT
    value = f"mean {mean:.3f}, judge_low {low:.2f}%"
    note = f"{data['pages']} pages, {data['scored_pages']} scored"
    return Bar(3, NAMES[2], value, threshold, passed, note=note)


def transcription_bar(data: dict[str, Any] | None) -> Bar:
    """Bar 4: `transcription-spotcheck.json`."""
    threshold = f"<= {MAX_INVENTED_PCT:g}%"
    if data is None or data.get("invented_pct") is None:
        return _missing(4, threshold)
    pct = float(data["invented_pct"])
    note = f"{data.get('n_lines', '?')} lines of {data.get('n_items', '?')} items"
    return Bar(4, NAMES[3], _pct(pct), threshold, pct <= MAX_INVENTED_PCT, note=note)


def _signer(record: dict[str, Any] | None, at_key: str) -> tuple[bool, str]:
    if not record or not record.get("name") or not record.get(at_key):
        return False, "not signed"
    if record["name"] == TEST_SIGNER:
        return False, f"signed by {TEST_SIGNER} (a test sign-off)"
    return True, f"signed by {record['name']} at {record[at_key]}"


def ocr_signoff_bar(data: dict[str, Any] | None) -> Bar:
    """Bar 5: `verdicts.json` signed off (name + date), not by the tester."""
    threshold = "signed"
    if data is None:
        return _missing(5, threshold)
    ok, note = _signer(data.get("signoff"), "signed_at")
    return Bar(5, NAMES[4], "signed" if ok else "not signed", threshold, ok, note=note)


def retrieval_bar(data: dict[str, Any] | None) -> Bar:
    """Bar 6: `baseline-compare.json` new recall@10 > old recall@10."""
    threshold = "new recall@10 > old"
    try:
        new, old = float(data["new"]["recall_at_10"]), float(data["old"]["recall_at_10"])
    except (TypeError, KeyError, ValueError):
        return _missing(6, threshold)
    value = f"new {new:.4f} vs old {old:.4f}"
    return Bar(6, NAMES[5], value, threshold, new > old, note=f"n={data['new'].get('n', '?')}")


def customer_bar(
    answers: dict[str, Any] | None, signoff: dict[str, Any] | None, answers_sha1: str | None
) -> Bar:
    """Bar 7: supported_pct >= 90 with n_failed = 0, and a sign-off of exactly these answers
    (sha1 of the current file) by someone other than the tester."""
    threshold = f"supported >= {MIN_SUPPORTED_PCT:g}%, n_failed = 0, signed"
    if answers is None or answers.get("supported_pct") is None:
        return _missing(7, threshold)
    pct, failed = float(answers["supported_pct"]), answers.get("n_failed")
    problems = []
    if pct < MIN_SUPPORTED_PCT:
        problems.append(f"supported {pct:.2f}% < {MIN_SUPPORTED_PCT:g}%")
    if failed != 0:
        problems.append(f"n_failed = {failed}")
    signed, sign_note = _signer(signoff, "signed_at")
    if signed and signoff.get("answers_sha1") != answers_sha1:
        signed, sign_note = False, "sign-off is for other answers (sha1 differs)"
    if not signed:
        problems.append(sign_note)
    value = f"{pct:.2f}% supported, {'signed' if signed else 'not signed'}"
    note = "; ".join(problems) or f"{answers.get('n_claims', '?')} claims; {sign_note}"
    return Bar(7, NAMES[6], value, threshold, not problems, note=note)


def verdict(bars: list[Bar]) -> tuple[str, str]:
    """(`PASS` | `FAIL`, the page header)."""
    failed = sum(1 for b in bars if not b.passed)
    if failed == 0 and len(bars) == len(NAMES):
        return "PASS", "PHASE 1: PASS"
    failed += len(NAMES) - len(bars)
    return "FAIL", f"PHASE 1: FAIL ({failed} bars)"
