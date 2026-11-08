# Board

Status: `draft` (not ready to build) -> `ready` (router may pick it up) -> `done` (merged into main).
Mode: `solo` (one worker in its own worktree) or `team` (teammates that talk to each other).
Runs: `alongside` (can run at the same time as others) or `waits for NNN: <reason>`.

Only the user marks tickets `ready`. Only the merger marks tickets `done`.

Source: `specs/grill/newspaper-rag-v2.md` (confirmed grill record; D-n / A-n ids below refer to it).

| # | Title | Status | Mode | Runs |
|---|-------|--------|------|------|
| 001 | Project foundation (repo, config, CLI, tests) | ready | solo | alongside |
| 002 | Backend stores (docker compose + clients) | draft | solo | waits for 001: needs package, config and CLI |
| 003 | Pipeline ledger and stage runner | draft | solo | waits for 002: ledger lives in Postgres |
| 004 | Unpack, normalize and register pages | draft | solo | waits for 003: registers pages in the ledger |
| 005 | Render pages and dev sample | draft | solo | waits for 004: needs registered pages |
| 006 | GPU model runner (one model at a time) | draft | solo | waits for 001: needs package and config |
| 007 | OCR stage (Surya layout + indic-ocr text) | draft | solo | waits for 005: needs page images; waits for 006: GPU lock |
| 008 | Split column-spanning blocks | draft | solo | waits for 007: edits the OCR block output |
| 009 | OCR sanity-check page and sign-off | draft | solo | waits for 008: shows split OCR blocks |
| 010 | Sarvam fallback for named page types | draft | solo | waits for 008: wraps the OCR block output |
| 011 | Edition and state from the folio | draft | solo | waits for 010: reads `ocr_final`; waits for 018: place → state list |
| 012 | VLM article grouping | draft | solo | waits for 010: reads `ocr_final`; waits for 006: model runner |
| 013 | Grouping judge and retry | draft | solo | waits for 012: grades its output |
| 014 | Grouping model bake-off | draft | solo | waits for 013: needs grouping + judge |
| 015 | Tables, infographics and notices transcription | draft | solo | waits for 013: needs final article groups |
| 016 | Continuation stitching (per date) | draft | solo | waits for 013: needs final article groups |
| 017 | Cross-edition story dedup (per date) | draft | solo | waits for 016: needs stitched articles; waits for 015: standalone table/notice items |
| 018 | District and mandal reference list | draft | solo | waits for 001: needs repo layout |
| 019 | Enrichment: category, people/orgs, event date | draft | solo | waits for 017: enriches canonical stories |
| 020 | Enrichment: places and districts | draft | solo | waits for 019: same enrich module; waits for 018: reference list |
| 021 | Enrichment: amounts as structured facts | draft | solo | waits for 020: same enrich module |
| 022 | Name alias table | draft | solo | waits for 021: same enrich module, uses its entities |
| 023 | Chunking | draft | solo | waits for 022: needs enriched stories; waits for 015: table/notice items |
| 024 | Embed and index into stores | draft | solo | waits for 023: needs chunks |
| 025 | Retrieval test set and hybrid search script | draft | solo | waits for 024: searches the stores |
| 026 | Embedding model bake-off | draft | solo | waits for 025: needs the retrieval test |
| 027 | Old-pipeline baseline comparison | draft | solo | waits for 025: needs the retrieval test and search script |
| 028 | Customer-question answer check | draft | solo | waits for 025: needs the search script |
| 029 | Pipeline orchestrator and incremental ingest | draft | solo | waits for 024: drives every stage up to indexing |
| 030 | Quality report | draft | solo | waits for 029: reads all run stats; waits for 026, 027, 028: their scores |
| 031 | Full-corpus run and phase-1 sign-off | draft | solo | waits for 030: needs report; waits for 014: chosen grouping model |
