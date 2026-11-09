"""Ticket 009: OCR sanity-check page selection, verdict store, sign-off rules and web page.

The server is driven through a small WSGI test client with a fake page (a PNG and an
`ocr_split`-shaped artifact in tmp_path), so nothing here needs the stores or the GPU.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

import pytest

from newsrag.ocr.check import pages as pages_mod
from newsrag.ocr.check import verdicts as v
from newsrag.ocr.check.server import App
from newsrag.stages.base import PageRow

TELUGU = "తెలంగాణలో భారీ ఎన్కౌంటర్"


# --- test client ---


class Response:
    def __init__(self, status: str, headers: list[tuple[str, str]], body: bytes) -> None:
        self.status_code = int(status.split()[0])
        self.headers = dict(headers)
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


class Client:
    """Calls the WSGI app directly."""

    def __init__(self, app: App) -> None:
        self.app = app

    def request(self, method: str, path: str, data: dict | list | None = None) -> Response:
        environ: dict = {}
        setup_testing_defaults(environ)
        path, _, query = path.partition("?")
        body = urlencode(data or {}, doseq=True).encode() if data is not None else b""
        environ.update(
            REQUEST_METHOD=method,
            PATH_INFO=path,
            QUERY_STRING=query,
            CONTENT_TYPE="application/x-www-form-urlencoded",
            CONTENT_LENGTH=str(len(body)),
        )
        environ["wsgi.input"] = io.BytesIO(body)
        captured: dict = {}

        def start_response(status, headers, exc_info=None):
            captured["status"], captured["headers"] = status, headers

        out = b"".join(self.app(environ, start_response))
        return Response(captured["status"], captured["headers"], out)

    def get(self, path: str) -> Response:
        return self.request("GET", path)

    def post(self, path: str, data: dict | list) -> Response:
        return self.request("POST", path, data)


# --- fixtures ---

PAGE_W, PAGE_H = 400, 600


def _blocks() -> list[dict]:
    return [
        {"block_id": "b001", "order": 1, "label": "SectionHeader", "type": "title",
         "bbox": [10, 10, 390, 60], "text": "సోమవారం"},
        {"block_id": "b003", "order": 3, "label": "Text", "type": "text",
         "bbox": [10, 200, 200, 400], "text": TELUGU + "\n10 కోట్లు"},
        {"block_id": "b002", "order": 2, "label": "Picture", "type": "figure",
         "bbox": [10, 70, 390, 190], "text": ""},
    ]  # fmt: skip


@pytest.fixture
def check(tmp_path):
    """(client, store, page_ids) for 10 fake pages that share one PNG."""
    from PIL import Image

    png = tmp_path / "page.png"
    Image.new("RGB", (PAGE_W, PAGE_H), "white").save(png)
    page_ids = [f"2025-01-{d:02d}_B_main_0{d % 9 + 1}" for d in range(1, 11)]
    artifacts = {}
    for page_id in page_ids:
        art = tmp_path / "art" / f"{page_id}.json"
        art.parent.mkdir(exist_ok=True)
        art.write_text(
            json.dumps(
                {
                    "page_id": page_id,
                    "image": {"path": str(png), "width": PAGE_W, "height": PAGE_H},
                    "blocks": _blocks(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        artifacts[page_id] = art
    store = v.Store(tmp_path / "out" / "verdicts.json", page_ids)
    app = App(
        page_ids,
        artifacts,
        store,
        tmp_path / "out" / "cache",
        image_long_side=240,
        image_max_bytes=2_000_000,
        jpeg_quality=85,
    )
    return Client(app), store, page_ids


def _all_verdicts(client: Client, page_ids: list[str], verdict="acceptable", problems=("numbers",)):
    for page_id in page_ids:
        r = client.post(f"/page/{page_id}", {"verdict": verdict, "problems": list(problems)})
        assert r.status_code == 303


# --- page selection (FR-1) ---


def _row(page_id: str) -> PageRow:
    day, edition, section, page = page_id.split("_")
    first, _, last = page.partition("-")
    return PageRow(
        page_id=page_id,
        pub_date=date.fromisoformat(day),
        edition_code=edition,
        section=section,
        page_num=int(first),
        page_end=int(last) if last else None,
        source_path="x",
        source_archive="x",
        revision=1,
    )


DEV = [
    "2024-12-02_BG_main_01",
    "2024-12-05_BGNOP_main_01",
    "2024-12-06_GNOCLT_main_16",
    "2024-12-08_BGNOPSVACLT_main_10",
    "2024-12-09_R_main_03",
    "2024-12-11_B_main_03",
    "2024-12-11_V_main_12",
    "2024-12-27_BGNOPRSVACLT_main_01",
    "2025-01-26_B_main_05",
    "2025-01-27_B_main_14",
    "2025-02-15_B_main_01",
    "2025-02-25_BGVC_main_14",
    "2025-05-04_BGNOPRSVACLT_main_06",
    "2025-05-11_BGNOPRSVACLT_main_03",
    "2025-10-10_BGNOPRSVACLT_main_01",
    "2025-10-15_B_main_10",
    "2025-10-20_ALL_main_02",
    "2025-11-03_BG_main_01",
    "2025-12-16_BG_main_06-07",
    "2025-12-17_B_main_01",
]


def test_choose_fills_every_slot():
    ids, short = pages_mod.choose(DEV, {i: _row(i) for i in DEV})
    assert short == []
    assert ids == [
        "2024-12-02_BG_main_01",
        "2024-12-05_BGNOP_main_01",
        "2024-12-06_GNOCLT_main_16",
        "2024-12-09_R_main_03",
        "2025-01-26_B_main_05",
        "2025-02-15_B_main_01",
        "2025-02-25_BGVC_main_14",
        "2025-10-10_BGNOPRSVACLT_main_01",
        "2025-10-20_ALL_main_02",
        "2025-12-16_BG_main_06-07",
    ]


def test_choose_uses_ledger_columns_not_the_id():
    from dataclasses import replace

    rows = {i: _row(i) for i in DEV}
    # The ledger says this page is page 5, whatever its id says: not a front page.
    rows["2024-12-02_BG_main_01"] = replace(rows["2024-12-02_BG_main_01"], page_num=5)
    ids, _ = pages_mod.choose(DEV, rows)
    assert "2024-12-27_BGNOPRSVACLT_main_01" in ids  # took the freed front slot
    assert "2024-12-02_BG_main_01" in ids  # now the first combined-code page != 1
    assert "2024-12-06_GNOCLT_main_16" not in ids


def test_choose_short_slot_takes_next_line_of_the_year():
    dev = [i for i in DEV if "_ALL_" not in i]
    ids, short = pages_mod.choose(dev, {i: _row(i) for i in dev})
    assert short == ["# short: 2025 all"]
    assert len(ids) == 10
    assert "2025-01-26_B_main_05" in ids  # first unused 2025 line fills the `all` slot


def test_choose_unknown_page_refuses():
    with pytest.raises(pages_mod.CheckError, match="not in ledger.pages"):
        pages_mod.choose(DEV, {})


@pytest.fixture
def build_env(tmp_path, monkeypatch):
    """build() with a fake ledger: settings in tmp_path, rows and artifacts patched."""
    from newsrag import config

    dev = tmp_path / "dev-sample.txt"
    dev.write_text("# comment\n" + "\n".join(DEV) + "\n", encoding="utf-8")
    out = tmp_path / "data" / "ocr-check.txt"
    s = config.settings.model_copy(
        update={"dev_sample_file": dev, "ocr_check_file": out, "repo_root": tmp_path}
    )
    monkeypatch.setattr(pages_mod, "settings", s)
    monkeypatch.setattr(pages_mod, "_page_rows", lambda ids: {i: _row(i) for i in ids})
    have = {i: tmp_path / f"{i}.json" for i in DEV}
    monkeypatch.setattr(
        pages_mod, "artifact_paths", lambda ids: {i: have[i] for i in ids if i in have}
    )
    return out, have


def test_build_writes_and_refuses_overwrite(build_env):
    out, _ = build_env
    path, ids, short = pages_mod.build()
    assert path == out and len(ids) == 10 and short == []
    assert out.read_text().splitlines() == ids
    with pytest.raises(pages_mod.CheckError, match=r"data/ocr-check.txt exists \(use --force\)"):
        pages_mod.build()
    first = out.read_text()
    pages_mod.build(force=True)
    assert out.read_text() == first


def test_build_refuses_missing_artifact(build_env):
    out, have = build_env
    del have["2025-10-20_ALL_main_02"]
    with pytest.raises(pages_mod.CheckError, match="missing ocr_split for: 2025-10-20_ALL_main_02"):
        pages_mod.build()
    assert not out.exists()


# --- sign-off rows and rules (FR-2, FR-3) ---


def test_row_to_json():
    assert v.row_to_json({"years": "2020", "page_nums": "1", "sections": "", "note": ""}) == {
        "years": [2020],
        "page_nums": [1],
    }
    assert v.row_to_json(
        {"years": "2024, 2025", "sections": "main", "edition_codes": "B,ALL", "note": " old "}
    ) == {"years": [2024, 2025], "sections": ["main"], "edition_codes": ["B", "ALL"], "note": "old"}
    assert v.row_to_json({"years": " ", "note": ""}) is None
    with pytest.raises(v.FormError, match="whole numbers"):
        v.row_to_json({"years": "twenty"})


def test_signoff_validation(check):
    client, store, page_ids = check
    _all_verdicts(client, page_ids)
    row = {"row-0-years": "2024", "row-0-note": ""}
    cases = [
        ({"name": "x", "no_page_types": "1", **row}, v.MSG_BOTH),
        ({"name": "x"}, v.MSG_NEITHER),
        ({"name": "  ", "no_page_types": "1"}, v.MSG_NAME),
        ({"name": "x", "row-0-years": "", "row-0-note": "headlines bad"}, v.MSG_NARROW),
    ]
    for form, message in cases:
        r = client.post("/signoff", form)
        assert r.status_code == 400, form
        assert f'<p class="error" role="alert">{message}</p>' in r.text
        assert store.read()["signoff"] is None
    # the reader's input is kept on the re-shown form
    r = client.post("/signoff", {"name": "Ravi", "no_page_types": "1", **row})
    assert 'value="Ravi"' in r.text and 'value="2024"' in r.text


def test_signoff_requires_all_pages(check):
    client, store, page_ids = check
    _all_verdicts(client, page_ids[:9])
    page = client.get("/signoff")
    assert "1 of 10 pages still need a verdict" in page.text
    assert '<button type="submit" disabled>Sign off</button>' in page.text
    r = client.post("/signoff", {"name": "eval", "no_page_types": "1"})
    assert r.status_code == 400
    assert "every page needs a verdict" in r.text
    assert store.read()["signoff"] is None
    _all_verdicts(client, page_ids[9:])
    assert "disabled>Sign off" not in client.get("/signoff").text


def test_signoff_rows_stored_and_change_clears_it(check, tmp_path):
    client, store, page_ids = check
    _all_verdicts(client, page_ids)
    form = {
        "name": "Ravi",
        "row-0-years": "2020",
        "row-0-page_nums": "1",
        "row-1-sections": "",  # an unused blank row is ignored
        "row-2-edition_codes": "FH, B",
        "row-2-note": "small type",
    }
    r = client.post("/signoff", form)
    assert r.status_code == 303
    saved = json.loads((tmp_path / "out" / "verdicts.json").read_text())
    assert saved["signoff"]["name"] == "Ravi"
    assert saved["signoff"]["failing_page_types"] == [
        {"years": [2020], "page_nums": [1]},
        {"edition_codes": ["FH", "B"], "note": "small type"},
    ]
    assert saved["pages"][page_ids[0]]["verdict"] == "acceptable"
    assert saved["pages"][page_ids[0]]["problems"] == ["numbers"]
    assert saved["pages"][page_ids[0]]["updated_at"]

    # the same verdict again changes nothing; a different one clears the sign-off
    client.post(f"/page/{page_ids[0]}", {"verdict": "acceptable", "problems": "numbers"})
    assert store.read()["signoff"] is not None
    r = client.post(f"/page/{page_ids[0]}", {"verdict": "good"})
    assert r.headers["Location"].endswith("?saved=1&cleared=1")
    assert store.read()["signoff"] is None
    assert "The sign-off was cleared" in client.get(r.headers["Location"]).text

    # signing again overwrites
    client.post("/signoff", {"name": "Lakshmi", "no_page_types": "1"})
    assert store.read()["signoff"]["name"] == "Lakshmi"
    assert store.read()["signoff"]["failing_page_types"] == []


def test_verdicts_survive_a_new_store(check, tmp_path):
    client, _store, page_ids = check
    client.post(f"/page/{page_ids[1]}", {"verdict": "bad", "problems": ["names", "tables"],
                                         "note": "పేర్లు తప్పు"})  # fmt: skip
    again = v.Store(tmp_path / "out" / "verdicts.json", page_ids)
    entry = again.read()["pages"][page_ids[1]]
    assert entry["verdict"] == "bad"
    assert entry["problems"] == ["names", "tables"]
    assert entry["note"] == "పేర్లు తప్పు"


def test_bad_verdict_post_rejected(check):
    client, store, page_ids = check
    r = client.post(f"/page/{page_ids[0]}", {"verdict": "great"})
    assert r.status_code == 400 and "choose a verdict" in r.text
    r = client.post(f"/page/{page_ids[0]}", {"verdict": "good", "problems": "spelling"})
    assert r.status_code == 400 and "unknown problem type" in r.text
    assert store.read()["pages"] == {}


def test_summary_lines(check):
    client, store, page_ids = check
    _all_verdicts(client, page_ids)
    lines = v.summary_lines(store.read(), page_ids)
    assert lines[:10] == [f"{p} acceptable numbers" for p in page_ids]
    assert lines[10:] == ["NOT SIGNED OFF", "none"]
    client.post("/signoff", {"name": "eval-tester", "row-0-years": "2024", "row-0-note": "x"})
    lines = v.summary_lines(store.read(), page_ids)
    assert lines[10].startswith("SIGNED OFF by eval-tester at ")
    assert lines[11:] == ['{"years": [2024], "note": "x"}']


# --- the web page (FR-2, FR-5, NFR-1) ---


def test_index_lists_pages_with_status(check):
    client, _, page_ids = check
    client.post(f"/page/{page_ids[0]}", {"verdict": "good"})
    r = client.get("/")
    assert r.status_code == 200
    for page_id in page_ids:
        assert f'href="/page/{page_id}"' in r.text
    assert r.text.count("not checked") == 9
    assert '<span class="v v-good">good</span>' in r.text
    assert "1 of 10 pages have a verdict" in r.text


def test_page_view_shows_scan_boxes_text_and_form(check):
    client, _, page_ids = check
    r = client.get(f"/page/{page_ids[0]}")
    assert r.status_code == 200
    assert f'src="/page/{page_ids[0]}/image.jpg"' in r.text
    assert f'href="/page/{page_ids[0]}/full.png"' in r.text
    assert f'viewBox="0 0 {PAGE_W} {PAGE_H}"' in r.text
    assert r.text.count('<g class="box') == 3
    assert TELUGU in r.text  # Telugu shown as-is
    # blocks in reading order, each with number and label
    pos = [r.text.index(f'<li class="block t-{t}" data-order="{n}">') for n, t in
           ((1, "title"), (2, "figure"), (3, "text"))]  # fmt: skip
    assert pos == sorted(pos)
    assert "(no text)" in r.text
    for word in (*v.VERDICTS, *v.PROBLEMS, "What is wrong"):
        assert word in r.text


def test_page_not_in_list_is_404(check):
    client, _, _ = check
    assert client.get("/page/not-a-page").status_code == 404
    assert client.get("/page/not-a-page/image.jpg").status_code == 404
    assert client.get("/nowhere").status_code == 404


def test_image_is_downscaled_jpeg_and_cached(check, tmp_path):
    from PIL import Image

    client, _, page_ids = check
    r = client.get(f"/page/{page_ids[0]}/image.jpg")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/jpeg"
    with Image.open(io.BytesIO(r.body)) as img:
        assert img.format == "JPEG" and max(img.size) == 240
    cached = tmp_path / "out" / "cache" / f"{page_ids[0]}.jpg"
    assert cached.read_bytes() == r.body
    full = client.get(f"/page/{page_ids[0]}/full.png")
    assert full.headers["Content-Type"] == "image/png"
    assert full.body == (tmp_path / "page.png").read_bytes()


def test_jpeg_size_cap_lowers_quality(tmp_path):
    import numpy as np
    from PIL import Image

    from newsrag.ocr.check.server import make_jpeg

    noise = np.random.default_rng(0).integers(0, 255, (800, 600, 3), dtype=np.uint8)
    src = tmp_path / "noise.png"
    Image.fromarray(noise).save(src)
    big = make_jpeg(src, tmp_path / "a.jpg", 800, 10_000_000, 95).stat().st_size
    capped = make_jpeg(src, tmp_path / "b.jpg", 800, big // 2, 95).stat().st_size
    assert capped <= big // 2


def test_static_font_and_assets(check):
    client, _, _ = check
    font = client.get("/static/NotoSansTelugu-Regular.woff2")
    assert font.status_code == 200
    assert font.headers["Content-Type"] == "font/woff2"
    assert font.body[:4] == b"wOF2"
    css = client.get("/static/check.css")
    assert "NotoSansTelugu-Regular.woff2" in css.text
    assert client.get("/static/check.js").status_code == 200
    assert client.get("/static/../server.py").status_code == 404
    licence = Path(__file__).parents[1] / "src/newsrag/ocr/check/static/OFL.txt"
    assert "SIL Open Font License" in licence.read_text()
