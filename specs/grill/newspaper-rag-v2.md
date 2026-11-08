# Grill: Newspaper RAG v2 — data processing with self-hosted Indic OCR

**Status:** confirmed (the user confirmed the play-back on 2026-11-08)
**Started:** 2026-11-08
**Rounds so far:** 8 + 2 gap-check passes

## Goal

Build the RAG over 18 years of (Telugu/English) newspapers. Step one is processing the data with really
good quality. Replace the Sarvam API with self-hosted `bodhan-ai/indic-ocr` on our own GPU. Redesign the
architecture, because the old code's results are poor. The new code goes in a new folder, which becomes
the main codebase for development and production. The old `RAG/` folder is not to be disturbed.

## Facts found

| # | Fact | Source |
|---|------|--------|
| F-1 | Data = 3 RAR5 archives, not 2: `2020.rar` (44.5 GB, 11,859 PDFs, Jan–Dec 2020 minus Sep–Oct), `2025.rar` (22.4 GB, 4,683 PDFs, Feb–Dec 2025), `2026.rar` (19.9 GB, 4,521 PDFs, Jan–Sep 2026). ≈21k PDFs, ≈87 GB unpacked. Also a stray `vinayaka.jpg`. | RAR header scan of `/home/admin1/data` |
| F-2 | One PDF = one page. Filename `{DD}{EDITION}-{MAIN\|FEA}_{NN}{R…}{_EPAPER}.pdf`. Edition = a letter run where a combination means one page shared by several editions (e.g. `BGNOPRSVACLT`). 2025 is almost all `FH` + `ALL`; 2020 and 2026 have many district codes. Revisions `R`/`RR`/`RRRR` and `_EPAPER` copies of the same page are common. Odd names such as `(VK)`, `(for Net)`, `-SPLUS` and `(RR)`: 6 in 2025, 181 in 2026, 397 in 2020. 2025 uses week folders (`01-04`, `05-11`); 2020 uses `main/out/`. | RAR header scan |
| F-3 | The old normalizer already handles day prefix, edition letters, revisions (highest R wins, non-epaper on a tie) and quarantine. It does not handle `-SPLUS`, `(VK)`, `(for Net)` or the 2025 week folders. | `RAG/ingest/normalize_ap2026.py` |
| F-4 | The embedded PDF text is a legacy font (Shree), so it can't be used and every page needs OCR. Old pipeline rendered at 300 DPI. | `RAG/pipeline/stage01_intake/run.py:59-79` |
| F-5 | Old architecture: an 8-stage chain driven by a SQLite manifest (`stage_base` claim→handle→mark), Surya layout + Sarvam OCR ("hybrid_v2" reorder), sequential article grouping, 400-token chunks, BGE-M3 embeddings, Qdrant + OpenSearch (ICU) + Postgres, FastAPI + Next.js UI, auth, history, feedback, citation verification. Corpus: 1,346 pages (AP, Jan–Jun 2026). | `RAG/CLAUDE.md`, explore report |
| F-6 | Why the old results were poor (documented in the handovers): (1) CER was never measured, no ground truth exists; (2) Sarvam garbles names and numbers; (3) columns fused inside one OCR block; (4) invented image descriptions leaked into results, and the fix hid all infographic and table text; (5) article grouping is purely sequential, so kickers and pull-quotes open fake articles, briefs columns merge several stories, mastheads and ads become articles, and ~50% of probe articles are low-confidence; (6) cross-page continuations ("తరువాయి") are never stitched; (7) the same story appears in many editions with no canonical story id, giving wrong or duplicate references; (8) highlight boxes are wrong because articles are mis-grouped; (9) the corpus covers only 6 months while questions span years, and enumeration questions can only see the top-k; (10) the quality gate was never baselined. | `RAG/handovers/*`, `RAG/docs/*`, `RAG/evals/*` |
| F-7 | `bodhan-ai/indic-ocr`: two stages, RT-DETR layout (33M, 37 classes, reading order) plus a Qwen3.5-0.8B block OCR. Input is a page image; output is reading-ordered Markdown plus per-block JSON (`order, label, bbox_xyxy, conf, text`). Telugu printed word accuracy 82.3% (Sarvam Vision 84.3, Gemini 3.1 Pro 85.5). It does not transcribe figures, charts or ads. Multi-column reading-order score 76. Gated HF repo; custom `indic_ocr` package; ~6 pages/s on an H100 with vLLM. License: Indic Open Model License: commercial use OK, but a hosted API for others needs sign-off. | HF model card (web-fetch) |
| F-8 | Machine: RTX PRO 6000 Blackwell, 96 GB VRAM (idle), 128 CPU cores, 121 GB RAM, 3.7 TB free disk. System Python 3.14. No `docker`, `uv`, `unrar` or `7z`. Not logged in to HF. admin1 is in the sudo group, but sudo needs a password. | `nvidia-smi`, `df`, `which`, `sudo -n` |
| F-9 | Production today: AWS EC2 m6i.2xlarge (no GPU) in ap-south-2, serving over plain HTTP from a restored store bundle. Answer models: Sarvam-105b and Claude Sonnet 4.5 on Bedrock. Customer eval: 30 Telugu questions, mostly multi-year, district-wise and before/after aggregates. | `RAG/docs/aws-live-deployment.md`, `RAG/evals/customer-questions.md` |

## Decisions

| # | Question | Answer | Why |
|---|----------|--------|-----|
| D-1 | What does phase 1 cover? | Ingestion all the way into the stores: unpack → indic-ocr → article segmentation → dedup → chunks/embeddings → Qdrant/OpenSearch/Postgres. The search/answer layer is a later phase. | user chose |
| D-2 | Self-hosting? | Ingestion is fully local on our GPU (OCR, layout, article grouping, tagging). Only the final answer LLM may stay external. | user chose |
| D-3 | How do we prove quality? | OCR is not the problem; the problems start after OCR. Sarvam OCR was fine and indic-ocr is also accurate, so do one sanity check of indic-ocr, with no gold transcription set. Quality effort goes into the post-OCR stages. | user's words |
| D-4 | New folder | `/home/admin1/news-rag`, a new git repo. It is the main dev and prod codebase. | user chose |
| D-5 | Article grouping | A local VLM plus geometry. indic-ocr provides the blocks; we draw numbered boxes on the page image; a local VLM (e.g. Qwen3-VL via vLLM) groups block numbers into articles and assigns roles (headline, kicker, byline, body…); geometry rules validate the result and flag doubtful pages. | user chose |
| D-6 | Checking segmentation quality | A model judge only (no human review set). | user chose |
| D-7 | Cross-edition duplicates | One canonical story per set of near-identical same-date articles, holding a list of every printing (edition, page). | user chose |
| D-8 | Edition names | Read from the page itself (folio/masthead text), not from a list the user supplies. | user chose |
| D-9 | Non-story content to make searchable | Tables and infographics (text and numbers, via a VLM pass, tied to their article), plus government notices and tenders (as their own items). Not selected: photo captions, commercial ads and classifieds. | user chose |
| D-10 | Cross-page continuations | Stitch them into one article; citations point to both page locations. | user chose |
| D-12 | Story metadata | Category; people, parties, departments and companies (Telugu and English spellings merged); places and districts (normalised); event date kept separate from print date; numbers and amounts as structured facts with what they refer to. All extracted locally. | user chose all four |
| D-13 | Embedding model | Test 2–3 local models (BGE-M3, Qwen3-Embedding, …) on a Telugu + English retrieval test built from our articles, and keep the winner. | user chose |
| D-14 | Phase 1 done | All ~21k pages (2020, 2025, 2026) are in the stores, plus a quality report covering: model-judge grouping score, OCR sanity check signed off, dedup and continuation stats, a retrieval test that beats the old pipeline on the same pages, and the 30 customer questions answered from the new stores by a test script. | user chose |
| D-15 | Old AWS production | Left running and untouched. The switch-over happens in phase 2, together with the new search/answer layer. | user chose |
| D-16 | Phase-1 check of the 30 customer questions | A local LLM only: a thin test script runs hybrid retrieval over the new stores and a local model writes the answers. No external call. | user chose |
| D-17 | If the indic-ocr sanity check fails on some page types | Allow Sarvam for those failing pages only. This is a deliberate exception to D-2 and needs a Sarvam key with credits. | user chose |
| D-18 | Districts | Store both: the current district (26 for AP) and the district at print time. A reference list of AP and Telangana districts and mandals is built once and kept in the repo. | user chose |
| D-19 | Meaning of the FH/ALL codes | Unknown. Read the place/edition name from each page's folio; if pages disagree, keep the raw code and list it in the report. | user chose |
| D-20 | Doubtful items (low judge score, geometry warning, unmatched continuation, unknown code) | Indexed with a `low_confidence` flag and a reason, and listed in the quality report. Nothing is held back. | user chose |
| D-21 | Pass bars for phase-1 done | Recall@10 beats the old pipeline; quarantine ≤ 1%; mean judge grouping score ≥ 0.90 with ≤ 5% of pages flagged; ≤ 2% invented text in the table/notice transcription spot-check; ≥ 90% of claims in the 30 customer answers judged supported by their cited sources, after which the user reads them. | user chose |
| D-11 | Quality vs speed | Quality first. Bigger local models are fine, and the current ~21k pages may take about a week of GPU time. Speed is re-evaluated before the 600k-page run. | user chose |

## Assumptions

| # | Assumption | Status |
|---|------------|--------|
| A-1 | Old `RAG/` is never modified; ideas and code may be copied from it. | confirmed |
| A-2 | The paper is Sakshi, a Telugu daily with AP and Telangana editions. | confirmed |
| A-3 | Revisions: the highest R wins; the `_EPAPER` copy loses a tie; losers are logged. | confirmed |
| A-4 | The RARs stay untouched; they are extracted to `/home/admin1/raw`, outside the repo. | confirmed |
| A-5 | Everything runs on this machine (RTX PRO 6000); no HPC. | confirmed |
| A-6 | The user runs the sudo installs (unrar, docker), accepts the indic-ocr HF licence and provides an HF token. | confirmed |
| A-7 | OCR sanity check: run indic-ocr on ~10 varied pages and have a Telugu reader compare them side by side with the scans. | confirmed |
| A-8 | Photo captions are not indexed. | confirmed |
| A-9 | The grouping judge is a different local model family from the grouping VLM, so the model never grades itself. Low-scoring pages are retried with a stronger setting; pages still low after that stay flagged. | confirmed |
| A-10 | Edition names are built once into a code→name file by reading the folios of single-letter pages. A shared-page code combination gets all of its names. | confirmed |
| A-11 | The retrieval unit is the story. Short stories are one chunk; long ones are split on paragraphs, each chunk prefixed with headline, date and edition. Each table is its own chunk, linked to its story. | confirmed |
| A-12 | Stores stay Qdrant + OpenSearch (ICU) + Postgres. Postgres also replaces SQLite as the pipeline ledger. | confirmed |
| A-13 | Bad PDFs and unparseable filenames are quarantined into a report, never guessed at. Every stage is resumable and safe to re-run. | confirmed |
| A-14 | Python 3.12 venv via uv (system Python 3.14 is too new for torch/vLLM); local models are served with vLLM. | confirmed |
| A-15 | Enrichment (category, entities, places, event date, amounts) uses a local text LLM, e.g. a Qwen3 30B-class model. | confirmed |
| A-16 | New year RARs dropped into `/home/admin1/data` later are ingested with the same command, without reprocessing pages already done. | confirmed |
| A-17 | Phase 1 has no user-facing UI, only an HTML quality report and an OCR side-by-side check page for the Telugu reader. | confirmed |
| A-18 | Only the user and the dev team operate the pipeline; there are no login roles in phase 1. | confirmed |
| A-19 | The stray `vinayaka.jpg` is ignored. | confirmed |
| A-21 | Pages are rendered at 300 DPI. The PNGs are kept in `/home/admin1/raw/images` (~250 GB) for overlays, the judge and the checks. Every page goes through OCR. | confirmed |
| A-22 | A two-page spread (`MAIN_08-09`) is one page record that carries both page numbers. | confirmed |
| A-23 | The edition code→name map is per year, since codes may change. State (AP or Telangana) is also read from the folio. Telangana pages are in scope if present. Unknown codes are flagged in the report. | confirmed |
| A-24 | 2025 dates take the month from the folder and the day from the filename prefix. | confirmed |
| A-25 | The VLM classifies every ad-like block as notice/tender or commercial; only notices and tenders are transcribed. Tables are stored as Markdown and linked to their story by the grouping VLM. | confirmed |
| A-26 | Dedup: articles from the same print date with text similarity ≥ 0.85 are merged. The canonical text is the longest printing. Edition and district filters store the list of all printings. A story reprinted the next day stays a separate story. | confirmed |
| A-27 | Continuations: found by the marker plus a page number on the same date, then matched by headline/keyword similarity. With no match, the part is kept on its own and flagged. | confirmed |
| A-28 | One big model is loaded at a time; stages run as separate passes over the corpus (bf16/FP8). Grouping VLM: the best open VLM from a 5-page bake-off (e.g. Qwen3-VL-32B). Judge: a different model family (e.g. Gemma or InternVL). Enrichment and answer LLM: a Qwen3 30B-class model. A judge score below 0.8 triggers a retry at higher resolution; pages still low after that are flagged. | confirmed |
| A-29 | Blackwell needs CUDA 12.8+ builds of torch and vLLM. `indic_ocr` gets its own venv if its pins clash. The user installs poppler as well. | confirmed |
| A-30 | The ledger records status and output version (model + prompt) per page and per stage. A page reaches the stores only after all its stages finish. A changed model reprocesses only the affected stages. A new embedding model goes into a new collection (blue-green). | confirmed |
| A-31 | Retrieval test: ~300 questions (70% Telugu, 30% English), written by a local LLM in its own words from sampled stories. The correct answer is matched by page plus text overlap, so old and new stores compare fairly. Metrics: recall@10 and MRR on hybrid search. "Beats the old pipeline" means a higher recall@10. A local judge scores the 30 customer answers and the user reads them. | confirmed |
| A-32 | A story of ≤ 512 tokens is one chunk. Longer stories are split on paragraphs, up to 512 tokens per chunk with a one-paragraph overlap. | confirmed |
| A-33 | Category keeps the old 7-value list. Name spellings are merged through an alias table that the local LLM proposes. Amounts go into a Postgres facts table (story, value, unit, what it refers to, date, place) and also stay in the chunk text. | confirmed |
| A-34 | "All pages" means the unique pages left after revision dedup. Quarantine may be at most 1% of them. | confirmed |
| A-35 | Storage for the future 600k-page run is planned before that run, not in phase 1. | confirmed |
| A-36 | If the D-17 fallback is needed, the user provides a Sarvam key with credits. | confirmed |
| A-37 | Odd file tags: `-SPLUS` is its own supplement section; `(VK)` is its own edition variant; `(for Net)` is a web copy that loses to the print page, as `_EPAPER` does; `(RR)` is revision RR. | confirmed (Q19) |
| A-38 | Sarvam fallback: after A-7 the Telugu reader names the failing page types, which become config rules (year/section/page). For those pages, each indic-ocr block crop goes to Sarvam, so our block numbers are kept. Sarvam's image descriptions are discarded. | confirmed |
| A-39 | A block that spans two columns is split at the column gap and re-OCR'd before grouping. | confirmed |
| A-40 | Tables, infographics and notices are transcribed by the grouping VLM in a separate pass. Infographics are stored as Markdown (title plus labelled values). | confirmed |
| A-41 | Dedup uses character-shingle MinHash ≥ 0.85, confirmed by embedding similarity. Clusters are transitive within a date. A story id is stable, derived from its first printing. | confirmed |
| A-42 | The chunk prefix is headline + print date + number of printings. The 512 tokens are counted with the chosen model's tokenizer, prefix included. | confirmed |
| A-43 | Dedup and stitching run per date, once all of that date's pages are grouped. Adding or redoing a page reruns only that date. | confirmed |
| A-44 | District lists come from official AP and Telangana government lists, with an as-of date; the AP count (26 or 28) is checked at build time. The district of the place in the story and the edition's district are stored separately. | confirmed |
| A-45 | Baseline: the new store is limited to the same Jan–Jun 2026 pages, and both stores are searched by the same plain hybrid-search script. | confirmed |
| A-46 | 2025 week folders: the month comes from the `Month-YYYY` folder; a day lower than the folder's start day rolls over to the next month. | confirmed |
| A-47 | Name-alias merges are applied automatically only at high confidence. The rest are listed for review and not applied. | confirmed |
| A-48 | Notices and tenders are chunked, deduplicated and enriched like stories, with department, notice number and deadline added. | confirmed |
| A-20 | Baseline: the user copies the old stage-9 export bundle from AWS to this machine. It is loaded into a separate store, and the same retrieval test runs on both for the shared Jan–Jun 2026 pages. | confirmed (Q16) |

## Out of scope

- The new search/answer API and UI, auth, history, and switching AWS production over: all phase 2 (D-1, D-15).
- Hand-made OCR gold transcriptions and a human article-review set (D-3, D-6).
- Photo captions, commercial ads and classifieds (D-9, A-8).
- Any change to `/home/admin1/RAG` (A-1).
- External APIs anywhere in ingestion (D-2). The one exception is the Sarvam fallback for named failing page types (D-17, A-38).

## Branches checked

| Branch | Covered by | Notes |
|--------|------------|-------|
| Goal | Goal, D-3 | Quality effort goes into post-OCR |
| Users and roles | A-18 | Dev team only |
| Scope | D-1, D-9, Out of scope | |
| Flows | D-5, D-7, D-10, A-11, A-16 | unpack → normalize → render → indic-ocr → VLM grouping → judge → continuation stitch → dedup → enrich → chunk → embed → index |
| States and data | F-1, A-4, A-12, D-12 | |
| Rules | A-3, A-10, D-7 | |
| Errors and edge cases | A-9, A-13 | |
| Integrations | F-7, A-6, A-14 | HF gated repo, vLLM |
| Non-functional | D-2, D-11 | Local only; quality over speed |
| Look and words | A-17 | No UI in phase 1 |
| Rollout | D-15, A-16 | |
| Done | D-14, A-20 | |

## Still unclear

- (none)

## Gap check

Pass 1 (`super-agent:gap-finder`, 24 findings). Each is resolved as follows:
- #1 answer test → D-16
- #2 comparison metric and id mapping → A-31
- #3 old/new page mapping → A-20 + A-31 (match by page + text overlap)
- #4 fused blocks → D-5 (the geometry check flags doubtful pages) + D-17
- #5 OCR fallback → D-17, A-36
- #6 districts → D-18
- #7 state → A-23
- #8 edition map per year → A-23
- #9 odd filenames and 2025 dates → A-37, A-24
- #10 done count → A-34
- #11 notices vs ads → A-25
- #12 tables → A-25
- #13 dedup rule → A-26
- #14 edition prefix and filters → A-26
- #15 continuations → A-27
- #16 GPU scheduling, judge, retry → A-28
- #17 Blackwell stack and poppler → A-29
- #18 render settings and disk → A-21, A-22, A-35
- #19 category, aliases, facts → A-33
- #20 retrieval test → A-31
- #21 chunk size → A-32
- #22 legacy-font check → A-21 (every page is OCR'd)
- #23 partial failure → A-30
- #24 reprocessing → A-30

Pass 2 (15 findings). Each is resolved as follows:
- #1 scope line vs Sarvam → the Out of scope line was fixed
- #2 Sarvam trigger and routing → A-38
- #3 fused columns → A-39
- #4 flagged items → D-20
- #5 pass bars → D-21
- #6 VLM transcription check → A-40 + D-21 spot-check
- #7 similarity measure → A-41
- #8 prefix and tokenizer → A-42
- #9 per-date stages → A-43
- #10 FH/ALL → D-19
- #11 district source → A-44
- #12 baseline method → A-45
- #13 week folders → A-46
- #14 alias approval → A-47
- #15 notices → A-48

A third pass was not run, since every pass-2 item now maps to an explicit user answer.

## Play-back

1. We build a new codebase at `/home/admin1/news-rag`, the main repo for dev and production from now on. The old `RAG/` folder is left untouched.
2. Phase 1 is data processing. It takes the 3 RAR archives (2020, 2025, 2026; ~21k PDF pages of Sakshi) all the way into Qdrant, OpenSearch and Postgres. Search, answers and UI come in phase 2, and old AWS production keeps running until then.
3. OCR moves from Sarvam to `bodhan-ai/indic-ocr` on our own RTX PRO 6000. OCR is not the real problem, so we only sanity-check it on ~10 pages. Sarvam is used again only for page types that fail that check.
4. The real fix is after OCR. A local vision model looks at each page with numbered blocks and groups them into proper articles (headline, kicker, byline, body). Geometry rules check the result, and a second local model from a different family grades it.
5. Continuations ("తరువాయి") are stitched into one article. The same story printed in many editions becomes one story listing all its printings.
6. Tables, infographics and government notices/tenders become searchable. Commercial ads, classifieds and photo captions are dropped.
7. Every story gets: category, people/parties/organisations (spellings merged), places with both old and new districts, event date separate from print date, and numbers/amounts as structured facts.
8. Edition and state names are read from the page folio. Odd filenames and revisions follow fixed rules; bad files are quarantined.
9. Everything in ingestion runs locally: quality first, about a week of GPU time is fine. Every stage is resumable and versioned, so a changed model reprocesses only what it affects.
10. We pick the best local embedding model on a Telugu/English retrieval test.
11. Phase 1 is done when all unique pages are in the stores (≤ 1% quarantined, doubtful items flagged) and these bars are met: mean judge score ≥ 0.90 with ≤ 5% of pages flagged, ≤ 2% invented text in transcriptions, recall@10 better than the old pipeline on the same Jan–Jun 2026 pages, and ≥ 90% supported claims in the 30 customer answers written by a local LLM, which the user then reads.

## Next steps

1. Create `/home/admin1/news-rag` (`git init`) and copy this record to `news-rag/specs/grill/newspaper-rag-v2.md`.
2. Save a memory: news-rag is the main dev/prod codebase, and `RAG/` is read-only reference.
3. Run `/super-agent:write-tickets specs/grill/newspaper-rag-v2.md` to turn this into build tickets. Nothing is built before the tickets are written.
4. User actions to start: `sudo apt install unrar poppler-utils`, install docker, accept the indic-ocr HF licence and provide an HF token, and scp the old stage-9 bundle from AWS.

## Added while writing tickets (2026-11-08)

Confirmed by the user after the per-ticket gap checks:

| # | Question | Answer | Why |
|---|----------|--------|-----|
| D-22 | Pages that still fail a later stage after bug fixes | Quarantined (`stage_failed:<stage>`); they count against the ≤ 1% quarantine bar | user chose |
| D-23 | A bar fails for a non-bug reason | The worker may tune prompts, model choices and retry settings (never the bars), rerun the affected stages within the time budget, and report what changed | user chose |
| A-49 | Telugu district/mandal spellings: official lists first; Telugu Wikipedia only for spellings missing there | confirmed |
| A-50 | Event dates may be up to 1 year after the print date (notices, exams, announced meetings) | confirmed |
| A-51 | A change in name aliases or edition names re-chunks and re-indexes the whole corpus (under 1 hour) | confirmed |
| A-52 | Final retrieval numbers come from a test set rebuilt on the full corpus (`retrieval-v2`) | confirmed |
| A-53 | Old-vs-new uses a dedicated 100-question set from pages both systems have, and runs only after a calibration shows ≥ 80% of old text can be matched | confirmed |
| A-54 | The embedding bake-off decides the embedding model for the full run | confirmed |
