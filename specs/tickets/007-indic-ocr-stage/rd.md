# 007: indic-ocr stage

**Status:** draft
**Mode:** solo
**Runs:** waits for 005: needs page images; waits for 006: GPU lock

## Summary

Replaces Sarvam with the self-hosted `bodhan-ai/indic-ocr` model on our GPU. Every rendered page gets
reading-ordered blocks (layout label, box, confidence, text) from indic-ocr's two-stage pipeline
(IndicDocLayout + IndicBlockOCR). This block list is the input for everything after OCR.

## Functional requirements

- **FR-1** - `uv run newsrag ocr install` downloads `bodhan-ai/indic-ocr` (with `settings.subprocess_env()`) into `settings.hf_home` at the revision pinned as `indic_ocr_revision` in `pipeline.yaml`; if no revision is pinned yet it takes the current HEAD and writes its commit hash into `pipeline.yaml` (committed, so the version never changes by accident). It creates a dedicated Python 3.12 venv `envs/indic-ocr/` by running the repo's `install.sh` with that venv active (torch build with CUDA ≥ 12.8 for Blackwell; if `install.sh` picks an older CUDA wheel, install the cu128+ wheel instead and note it in `results.md`). Re-running is idempotent (an existing venv at the pinned revision is kept). The worker verifies the package API against the downloaded snapshot (`README`, `indic_ocr/`), and any difference from this ticket's names is recorded in `results.md`. If the token is missing or the licence was not accepted, it exits 2 with `cannot download bodhan-ai/indic-ocr: accept the licence on huggingface.co and set HF_TOKEN in .env`.
- **FR-2** - A standalone worker script `src/newsrag/ocr/indic_worker.py` (stdlib + `indic_ocr` only; it does not import `newsrag`) runs inside `envs/indic-ocr/`, started by `src/newsrag/ocr/client.py` with `--repo <snapshot path>` and `settings.subprocess_env()`. It loads the model once, prints `model loaded in <s> s` to stderr, then reads one JSON request per stdin line and writes one JSON line per request:
  - `{"op": "page", "image": path}` → the full two-stage result (layout + block OCR): `{"blocks": [...], "markdown": "..."}`;
  - `{"op": "blocks", "image": path, "blocks": [{"bbox": [x1,y1,x2,y2], "type": "text"}]}` → block OCR only on the given boxes (via `IndicBlockOCR` with that layout): `{"texts": [str, ...]}` in the same order;
  - any per-request error → `{"error": "<message>"}` (the worker keeps running).
  The client restarts the worker if it dies (EOF / non-zero exit), applies a per-request timeout (`ocr_request_timeout_s`, default 300 s; on timeout the worker is killed and restarted and the item fails), and is the only way other tickets (008, 011) call indic-ocr. The main venv never imports indic-ocr.
- **FR-3** - PageStage `ocr` (`depends_on=("render",)`, `version = "indic-ocr@<indic_ocr_revision[:8]>-v1"`, computed lazily; `preflight()` raises `StageUnavailable("indic-ocr not installed: run uv run newsrag ocr install")` when the venv or pinned snapshot is missing; `concurrency = 1`). `open()` takes `newsrag.models.gpu_lock("indic-ocr")` and starts the worker; `close()` stops both. Each page's PNG is sent as an `op: page` request and the artifact JSON is `{page_id, image:{path,width,height}, model:{repo, revision}, blocks:[{block_id, order, label, type, bbox:[x1,y1,x2,y2], conf, text}], markdown}`. **All** detected blocks of every label are kept (text `""` when indic-ocr does not transcribe that label). `order` = 1..n in indic-ocr's reading order (renumbered if the model is 0-based); `block_id` = `b` + 3-digit order (`b001`); `bbox` is in pixels of the 300 DPI PNG (rescaled if the model returns another coordinate space); `type` = the label's group from `labels.yaml` (FR-6).
- **FR-4** - Detail = `{n_blocks, n_text_blocks (blocks whose group is `text` or `title`), label_counts, chars, zero_blocks}`; a page with 0 blocks is `done` with `zero_blocks: true` (it is flagged later, not failed).
- **FR-5** - `uv run newsrag ocr show <page_id> [--stage ocr|ocr_split|ocr_final]` reads the artifact of that stage's current `done` row and prints one line per block: `order<TAB>label<TAB>x1,y1,x2,y2<TAB>first 60 chars of text`; with no artifact it prints `no <stage> artifact for <page_id>` and exits 1. At the end of a `stage run ocr` the stage prints `ocr: <n> pages in <s> s (excl. model load) = <r> pages/s`.
- **FR-6** - `labels.yaml` (repo root, committed), generated once by `uv run newsrag ocr labels` from the downloaded layout model's label list (`id2label` in its config) and then reviewed by the worker: it maps every one of the 37 labels to exactly one group — `text` (body text, paragraphs, lists), `title` (headlines, section titles), `header_footer` (page header, footer, folio), `page_number`, `table`, `figure` (photos, images), `chart` (charts, infographics), `advertisement`, `caption`, `other`. Settings `text_labels`, `title_labels`, `folio_labels`, `table_labels`, `figure_labels`, `ad_labels` are derived from it, and tickets 008, 010–015 use these settings instead of raw label strings.
- **FR-7** - Clarifications (second review):
  - Settings (in `config.py`, `# --- ocr ---`): `indic_ocr_revision`, `ocr_request_timeout_s`, and label sets derived from `labels.yaml` groups: `text_labels` = text; `title_labels` = title; `folio_labels` = header_footer + page_number; `table_labels` = table; `figure_labels` = figure; `chart_labels` = chart; `ad_labels` = advertisement; `caption_labels` = caption.
  - The worker runs in its own process group with `PR_SET_PDEATHSIG`; timeout/close kills the whole group. The worker uses the package's default backend; if that is vLLM it sets `gpu_memory_utilization` 0.85, and the choice is recorded in `results.md`.
  - The `ocr` version includes a hash of `labels.yaml` (ticket 003 config hash). A label missing from `labels.yaml` maps to group `other` and is counted in detail `unknown_labels`; `preflight()` also requires `labels.yaml`. `labels.yaml` is committed by this ticket (the eval relies on it).
  - Detail key `n_text_blocks` counts group `text` only; `n_title_blocks` counts group `title` (ticket 008 uses the same meanings). Text is NFC-normalised in the client, for every request type.

## Non-functional requirements

- **NFR-1** - Throughput on this GPU ≥ 0.5 pages/s on the dev sample (measured over the 20 pages, excluding model load).
- **NFR-2** - Telugu text is stored as NFC-normalised Unicode; no `�` replacement characters in more than 0.1% of characters on the dev sample.

## Affected areas

- `src/newsrag/ocr/__init__.py`, `install.py`, `indic_worker.py`, `client.py` (new)
- `src/newsrag/stages/ocr.py`, `src/newsrag/stages/registry.py` (new / changed)
- `src/newsrag/cli.py` - `ocr` group (changed)
- `tests/test_ocr_stage.py` (new; a fake worker for unit tests, a `gpu`+`data` test on 2 real pages)

## Don't touch

- `envs/vllm/` from ticket 006; `/home/admin1/RAG/**`.

## Out of scope

- Splitting column-spanning blocks (008), reading header/folio text (011), tables/figures transcription (015), any Sarvam call (010).

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | OCR engine | `bodhan-ai/indic-ocr`, self-hosted | grill goal, D-2 |
| D-2 | Isolation | separate venv + stdin/stdout worker | grill A-29 (pins may clash) |
| D-3 | Table format from indic-ocr | HTML, kept raw | default of the package; ticket 015 produces the Markdown that is indexed |
| D-4 | Empty pages | done + `zero_blocks` flag | grill D-20: doubtful items are flagged, not held back |
| D-5 | Revision | pinned in `pipeline.yaml` | re-installing must never change the version and rerun the whole corpus |
| D-6 | Label names | one committed `labels.yaml` with groups | later tickets need exact labels; the real names come from the model |
| D-7 | Worker protocol | `page` and `blocks` ops, error replies, client restarts the worker | ticket 008/011 need block-only OCR; one bad page must not fail the rest |

## Open questions

None.
