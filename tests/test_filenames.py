"""Filename rules (ticket 004, FR-3, FR-5, NFR-1). Names are real ones from the archives."""

from datetime import date

import pytest

from newsrag.ingest.filenames import ParsedPage, Rejected, clean_name, parse

ROOT = "/home/admin1/raw/source"

# (path under ROOT, date, edition, section, page, page_end, revision, copy_kind)
# fmt: off
CASES = [
    ("2025/April-2025/01-04/01-04-2025/01FH-MAIN_03_EPAPER.pdf",
     date(2025, 4, 1), "FH", "main", 3, None, 0, "epaper"),
    ("2025/April-2025/26-30/02FH-MAIN_05R.pdf",
     date(2025, 5, 2), "FH", "main", 5, None, 1, "print"),
    ("2026/April-2026/03-04-2026/03BGNOPRSVACLT-MAIN_17R-SPLUS.pdf",
     date(2026, 4, 3), "BGNOPRSVACLT", "splus", 17, None, 1, "print"),
    ("2020/April-2020/02-04-2020/main/out/02FH-MAIN01(VK)R.pdf",
     date(2020, 4, 2), "FH-VK", "main", 1, None, 1, "print"),
    ("2020/April-2020/06-04-2020/main/out/06E-MAIN_10(for Net).pdf",
     date(2020, 4, 6), "E", "main", 10, None, 0, "web"),
    ("2025/April-2025/21-25/21FH-MAIN_01(RR).pdf",
     date(2025, 4, 21), "FH", "main", 1, None, 2, "print"),
    ("2025/April-2025/21-25/25FH-MAIN_02R .pdf",
     date(2025, 4, 25), "FH", "main", 2, None, 1, "print"),
    ("2025/April-2025/19-20/19FH-MAIN_08R\u00ad.pdf",
     date(2025, 4, 19), "FH", "main", 8, None, 1, "print"),
    ("2025/April-2025/12-18/13FH-MAIN_14-EPAPER.pdf",
     date(2025, 4, 13), "FH", "main", 14, None, 0, "epaper"),
    ("2025/April-2025/01-04/01FH_MAIN_11R.pdf",
     date(2025, 4, 1), "FH", "main", 11, None, 1, "print"),
    ("2026/September2026/02-09-2026/02FH-MAIN_05.pdf",
     date(2026, 9, 2), "FH", "main", 5, None, 0, "print"),
    # From the ZIP delivery (2024.zip, 2025.zip).
    ("2025/October-2025/10-10-2025/main/out/10B-MAIN_11RRRR.ps(EPAPER).pdf",
     date(2025, 10, 10), "B", "main", 11, None, 4, "epaper"),
    ("2025/May-2025/03-05-2025/main/out/03B-MAIN_03R(E-PAPER).pdf",
     date(2025, 5, 3), "B", "main", 3, None, 1, "epaper"),
    ("2025/May-2025/03-05-2025/main/out/03B-MAIN_03R(E PAPER).pdf",
     date(2025, 5, 3), "B", "main", 3, None, 1, "epaper"),
    ("2025/May-2025/03-05-2025/main/out/03B-MAIN_03R(Epapar).pdf",
     date(2025, 5, 3), "B", "main", 3, None, 1, "epaper"),
    ("2025/May-2025/03-05-2025/main/out/03B-MAIN_03R(E-PEPAR).pdf",
     date(2025, 5, 3), "B", "main", 3, None, 1, "epaper"),
    ("2025/May-2025/03-05-2025/main/out/03B-MAIN_03R.(EPAPER)ps.pdf",
     date(2025, 5, 3), "B", "main", 3, None, 1, "epaper"),
    ("2025/May-2025/03-05-2025/main/out/03B-MAIN_03R(E_PAPER).pdf",
     date(2025, 5, 3), "B", "main", 3, None, 1, "epaper"),
    ("2024/December-2024/03-12-2024/main/out/03B-MAIN_14RRR.pdf",
     date(2024, 12, 3), "B", "main", 14, None, 3, "print"),
    ("2024/December-2024/20-12-2024/main/out/20NORV-MAIN_03.ps.pdf",
     date(2024, 12, 20), "NORV", "main", 3, None, 0, "print"),
    # Other rules.
    ("2026/January-2026/12-01-2026/12B-MAIN_08-09.pdf",
     date(2026, 1, 12), "B", "main", 8, 9, 0, "print"),
    ("2026/April-2026/04-04-2026/04ALL-FEA_02R.pdf",
     date(2026, 4, 4), "ALL", "fea", 2, None, 1, "print"),
    ("2026/April-2026/04-04-2026/04fh-MAI_07r.pdf",
     date(2026, 4, 4), "FH", "main", 7, None, 0, "print"),
    ("2025/December-2025/29-31/02FH-MAIN_01.pdf",
     date(2026, 1, 2), "FH", "main", 1, None, 0, "print"),
    ("2025/April-2025/01-04/01FH-MAIN_06RR-SPLUS.PDF",
     date(2025, 4, 1), "FH", "splus", 6, None, 2, "print"),
    ("2025/April-2025/01-04/01FH-MAIN_09\u2008R.pdf",
     date(2025, 4, 1), "FH", "main", 9, None, 1, "print"),
]
# fmt: on


@pytest.mark.parametrize(
    ("rel", "pub_date", "edition", "section", "page", "page_end", "revision", "copy_kind"),
    CASES,
    ids=[c[0].replace("\u00ad", "<shy>").replace("\u2008", "<sp>") for c in CASES],
)
def test_parse(rel, pub_date, edition, section, page, page_end, revision, copy_kind):
    got = parse(f"{ROOT}/{rel}")
    assert isinstance(got, ParsedPage), got
    assert (got.date, got.edition_code, got.section, got.page, got.page_end) == (
        pub_date,
        edition,
        section,
        page,
        page_end,
    )
    assert (got.revision, got.copy_kind) == (revision, copy_kind)
    assert got.path == f"{ROOT}/{rel}"


@pytest.mark.parametrize(
    ("rel", "reason"),
    [
        ("2025/misc/01FH-MAIN_01.pdf", "no_date"),
        ("xyz.pdf", "no_date"),
        ("2020/April-2020/01-04-2020/01FH-DUMMY_01.pdf", "test_or_dummy"),
        ("2020/April-2020/01-04-2020/01FH-MAIN_01TEST.pdf", "test_or_dummy"),
        ("2020/April-2020/01-04-2020/01FH-MAIN_01_test.pdf", "test_or_dummy"),
        ("2024/December-2024/18-12-2024/main/out/18V_10.pdf", "bad_filename"),
        ("2020/April-2020/01-04-2020/notes.pdf", "bad_filename"),
        ("2025/April-2025/26-30/31FH-MAIN_01.pdf", "bad_date"),
        ("2025/April-2025/31-04-2025/31FH-MAIN_01.pdf", "bad_date"),
    ],
)
def test_rejected(rel, reason):
    got = parse(f"{ROOT}/{rel}")
    assert got == Rejected(reason, f"{ROOT}/{rel}")


def test_test_folder_does_not_reject():
    # Only the file name counts for test_or_dummy, not the folders.
    got = parse(f"{ROOT}/TEST/2025/April-2025/01-04-2025/01FH-MAIN_01.pdf")
    assert isinstance(got, ParsedPage)


def test_date_folder_wins_over_filename_day():
    got = parse(f"{ROOT}/2020/April-2020/02-04-2020/main/out/03FH-MAIN_01.pdf")
    assert isinstance(got, ParsedPage)
    assert got.date == date(2020, 4, 2)
    assert got.day_mismatch is True
    same = parse(f"{ROOT}/2020/April-2020/02-04-2020/main/out/02FH-MAIN_01.pdf")
    assert same.day_mismatch is False


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        (
            "2026/April-2026/01-04-2026/01BGNOPRSVACLT-MAIN_01.pdf",
            "2026-04-01_BGNOPRSVACLT_main_01",
        ),
        ("2020/April-2020/02-04-2020/main/out/02FH-MAIN01(VK)R.pdf", "2020-04-02_FH-VK_main_01"),
        ("2026/April-2026/01-04-2026/01FH-MAIN_17R-SPLUS.pdf", "2026-04-01_FH_splus_17"),
        ("2026/January-2026/12-01-2026/12B-MAIN_08-09.pdf", "2026-01-12_B_main_08-09"),
    ],
)
def test_page_id(rel, expected):
    assert parse(f"{ROOT}/{rel}").page_id == expected


def test_clean_name():
    assert clean_name(" 19FH-MAIN_08R\u00ad.pdf ") == "19FH-MAIN_08R.pdf"
    assert clean_name("01FH-MAIN_09\u2008R.pdf") == "01FH-MAIN_09R.pdf"
