# 007: OCR stage (Surya layout + indic-ocr text)

**Status:** done
**Mode:** solo
**Runs:** waits for 005: needs page images; waits for 006: GPU lock

## Summary

Replaces Sarvam with self-hosted models on our GPU, in two steps (grill D-24). **Surya** finds the
blocks on every page and their reading order. It is used because indic-ocr's own layout model was not
trained mainly on newspapers. **`bodhan-ai/indic-ocr`** (its IndicBlockOCR text model) then reads the
Telugu/English text inside each Surya block. The resulting block list (label, box, reading order,
confidence, text) is the input for everything after OCR.

## Functional requirements

- **FR-1** - `uv run newsrag ocr install` sets up both models, idempotently:
  - **indic-ocr:** downloads `bodhan-ai/indic-ocr` (with `settings.subprocess_env()`) into `settings.hf_home` at the revision pinned as `indic_ocr_revision` in `pipeline.yaml`. If none is pinned yet it takes the current HEAD and writes the commit hash with `newsrag.config.set_pipeline_key`. It creates a Python 3.12 venv `envs/indic-ocr/` by running the repo's `install.sh` with that venv active (torch for CUDA ≥ 12.8; if `install.sh` picks an older CUDA wheel, install the cu128+ wheel instead and note it in `results.md`). If the token is missing or the licence was not accepted, it exits 2 with `cannot download bodhan-ai/indic-ocr: accept the licence on huggingface.co and set HF_TOKEN in .env`.
  - **Surya:** creates a Python 3.12 venv `envs/surya/` with the newest `surya-ocr` release that runs on Blackwell (CUDA ≥ 12.8), pins that version as `surya_version` in `pipeline.yaml`, and downloads its layout model into `hf_home`. Reference adapter: `/home/admin1/RAG/pipeline/common/surya_engine.py` (written for surya-ocr 0.20.0; the API may differ in newer releases).
  - Re-running keeps existing venvs at the pinned versions. The worker checks both package APIs against the installed code and records any difference from this ticket's names in `results.md`.
- **FR-2** - Two standalone worker scripts (stdlib + their own package only; they do not import `newsrag`), each started by `src/newsrag/ocr/client.py` with `settings.subprocess_env()` in its own process group with `PR_SET_PDEATHSIG`. Each loads its model once, prints `model loaded in <s> s` to stderr, then reads one JSON request per stdin line and writes one JSON line per request; any per-request error is answered with `{"error": "<message>"}` and the worker keeps running:
  - `src/newsrag/ocr/surya_worker.py` (in `envs/surya/`): `{"op": "layout", "image": path}` → `{"regions": [{"bbox": [x1,y1,x2,y2], "label", "position", "confidence"}], "width", "height"}`. Rectangles come from Surya's polygons, sorted by Surya's reading order (`position`).
  - `src/newsrag/ocr/indic_worker.py` (in `envs/indic-ocr/`, started with `--repo <snapshot path>`): `{"op": "blocks", "image": path, "blocks": [{"bbox": [...], "type": "text"|"table"}]}` → `{"texts": [str, ...]}` in the same order, using indic-ocr's block OCR (`IndicBlockOCR`) with a caller-given layout. indic-ocr's own layout model is never used.
  - The client restarts a worker that dies (EOF or non-zero exit). It applies a per-request timeout (`ocr_request_timeout_s`, default 300 s); on timeout it kills the worker's process group, restarts it, and fails that item. All text is NFC-normalised in the client. The client is the only way other tickets (008, 011) call either model; the main venv imports neither package.
- **FR-3** - PageStage `ocr` (`depends_on=("render",)`, `version = "surya@<surya_version>+indic-ocr@<indic_ocr_revision[:8]>-v1"` plus ticket 003's config hash, which covers `labels.yaml`; computed lazily; `concurrency = 1`). `preflight()` raises `StageUnavailable("OCR models not installed: run uv run newsrag ocr install")` when a venv, the pinned snapshot or `labels.yaml` is missing. `open()` takes `newsrag.models.gpu_lock("ocr")` and starts both workers; both models fit in GPU memory together, so they stay loaded for the whole run. `close()` stops them and releases the lock. For each page:
  1. a `layout` request to Surya gives the regions;
  2. regions whose group (FR-6) is `text`, `title`, `header_footer`, `page_number` or `caption` are sent in one `blocks` request with `type: "text"`; regions of group `table` are sent with `type: "table"` (indic-ocr returns HTML for tables). Regions of group `figure`, `chart`, `advertisement` or `other` are not sent (their text stays `""`; ticket 015 handles them);
  3. the artifact JSON is `{page_id, image:{path,width,height}, model:{layout: "surya@<v>", ocr: {repo, revision}}, blocks:[{block_id, order, label, type, bbox:[x1,y1,x2,y2], conf, text}], markdown}`. **All** Surya regions are kept. `order` = 1..n in Surya's reading order; `block_id` = `b` + 3-digit order (`b001`); `bbox` in pixels of the 300 DPI PNG (scaled if Surya worked on a resized image); `label` = Surya's label; `type` = the label's group; `conf` = Surya's confidence; `markdown` = the block texts in order joined by blank lines.
- **FR-4** - Detail = `{n_blocks, n_text_blocks (group text), n_title_blocks (group title), label_counts, chars, zero_blocks, unknown_labels}`. A page where Surya finds 0 regions is `done` with `zero_blocks: true`. No fallback layout is used (grill D-24); ticket 012 flags such pages `low_confidence:no_blocks`.
- **FR-5** - `uv run newsrag ocr show <page_id> [--stage ocr|ocr_split|ocr_final]` (default: the newest done of `ocr_final`, `ocr_split`, `ocr`) reads that stage's current artifact and prints one line per block: `order<TAB>label<TAB>x1,y1,x2,y2<TAB>first 60 chars of text`. With no artifact it prints `no <stage> artifact for <page_id>` and exits 1. At the end of a `stage run ocr` the stage prints `ocr: <n> pages in <s> s (excl. model load) = <r> pages/s`.
- **FR-6** - `labels.yaml` (repo root, committed by this ticket) is generated by `uv run newsrag ocr labels` from Surya's layout label list and then reviewed by the worker. It maps every Surya label to exactly one group: `text` (Text, ListItem, Footnote, TextInlineMath …), `title` (Title, SectionHeader), `header_footer` (PageHeader, PageFooter), `page_number`, `table` (Table, Form, TableOfContents), `figure` (Picture, Figure), `chart`, `advertisement`, `caption` (Caption), `other`. Settings in `config.py` (`# --- ocr ---` block): `surya_version`, `indic_ocr_revision`, `ocr_request_timeout_s`, and label sets derived from the groups — `text_labels` = text; `title_labels` = title; `folio_labels` = header_footer + page_number; `table_labels` = table; `figure_labels` = figure; `chart_labels` = chart; `ad_labels` = advertisement; `caption_labels` = caption. Tickets 008 and 010–015 use these settings, never raw label strings. A label missing from `labels.yaml` maps to `other` and is counted in `unknown_labels`. If Surya has no separate chart or advertisement label, those groups stay empty: ticket 012's article grouping identifies ads by kind, and ticket 015 reads charts and figures.

## Non-functional requirements

- **NFR-1** - Throughput on this GPU ≥ 0.5 pages/s on the dev sample (20 pages, model load excluded).
- **NFR-2** - Telugu text is stored as NFC-normalised Unicode; no `�` replacement characters in more than 0.1% of characters on the dev sample.

## Affected areas

- `src/newsrag/ocr/__init__.py`, `install.py`, `surya_worker.py`, `indic_worker.py`, `client.py`, `cli.py` (new)
- `src/newsrag/stages/ocr.py`, `src/newsrag/stages/registry.py` (new / changed)
- `src/newsrag/cli.py` - register the `ocr` group (changed); `src/newsrag/config.py` - `# --- ocr ---` block (changed)
- `labels.yaml` (new, committed); `pipeline.yaml` - `surya_version`, `indic_ocr_revision` (changed via `set_pipeline_key`)
- `tests/test_ocr_stage.py` (new; fake workers for unit tests, a `gpu`+`data` test on 2 real pages)

## Don't touch

- `envs/vllm/` from ticket 006; `/home/admin1/RAG/**` (the Surya adapter there is reference only).

## Out of scope

- indic-ocr's own layout model (not used, grill D-24); splitting column-spanning blocks (008); reading header/folio text again (011); figures, charts and notices (015); any Sarvam call (010).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Text engine | `bodhan-ai/indic-ocr` block OCR, self-hosted | grill goal, D-2 |
| D-2 | Layout engine | Surya on every page, no fallback | grill D-24 (user chose): indic-ocr's layout model was not trained mainly on newspapers |
| D-3 | Isolation | one venv and stdin/stdout worker per model | grill A-29 (pins may clash) |
| D-4 | Tables | sent to indic-ocr as `type: table`, HTML kept raw | ticket 015 produces the Markdown that gets indexed |
| D-5 | Empty pages | done with `zero_blocks`, flagged later | grill D-20: doubtful items are flagged, not held back |
| D-6 | Versions | Surya version and indic-ocr revision pinned in `pipeline.yaml` | re-installing must never silently rerun the whole corpus |
| D-7 | Label names | one committed `labels.yaml` mapping Surya labels to groups | later tickets need stable label sets |
| D-8 | Both models loaded together | yes, under one GPU lock for the run | Surya layout and indic-ocr's 0.8B text model are small; reloading per page would be slow |

## Open questions

None.
