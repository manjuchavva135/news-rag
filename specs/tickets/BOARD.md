# Board

Status: `draft` (not ready to build) -> `ready` (router may pick it up) -> `done` (merged into main).
Mode: `solo` (one worker in its own worktree) or `team` (teammates that talk to each other).
Runs: `alongside` (can run at the same time as others) or `waits for NNN: <reason>`.

Only the user marks tickets `ready`. Only the merger marks tickets `done`.

Source: `specs/grill/newspaper-rag-v2.md` (confirmed grill record; D-n / A-n ids below refer to it).

| # | Title | Status | Mode | Runs |
|---|-------|--------|------|------|
| 001 | Project foundation (repo, config, CLI, tests) | done | solo | alongside |
| 002 | Backend stores (docker compose + clients) | done | solo | waits for 001: needs package, config and CLI |
| 003 | Pipeline ledger and stage runner | done | solo | waits for 002: ledger lives in Postgres |
| 004 | Unpack, normalize and register pages | done | solo | waits for 003: registers pages in the ledger |
| 005 | Render pages and dev sample | done | solo | waits for 004: needs registered pages |
| 006 | GPU model runner (one model at a time) | done | solo | waits for 001: needs package and config |
| 007 | OCR stage (Surya layout + indic-ocr text) | done | solo | waits for 005: needs page images; waits for 006: GPU lock |
| 008 | Split column-spanning blocks | done | solo | waits for 007: edits the OCR block output |
| 009 | OCR sanity-check page and sign-off | done | solo | waits for 008: shows split OCR blocks |
| 010 | Sarvam fallback for named page types | done | solo | waits for 008: wraps the OCR block output |
| 011 | Edition and state from the folio | done | solo | waits for 010: reads `ocr_final`; waits for 018: place → state list |
| 012 | VLM article grouping | done | solo | waits for 010: reads `ocr_final`; waits for 006: model runner |
| 013 | Grouping judge and retry | done | solo | waits for 012: grades its output |
| 014 | Grouping model bake-off | done | solo | waits for 013: needs grouping + judge |
| 015 | Tables, infographics and notices transcription | done | solo | waits for 013: needs final article groups |
| 016 | Continuation stitching (per date) | done | solo | waits for 013: needs final article groups |
| 017 | Cross-edition story dedup (per date) | done | solo | waits for 016: needs stitched articles; waits for 015: standalone table/notice items |
| 018 | District and mandal reference list | done | solo | waits for 001: needs repo layout |
| 019 | Enrichment: category, people/orgs, event date | done | solo | waits for 017: enriches canonical stories |
| 020 | Enrichment: places and districts | done | solo | waits for 019: same enrich module; waits for 018: reference list |
| 021 | Enrichment: amounts as structured facts | done | solo | waits for 020: same enrich module |
| 022 | Name alias table | done | solo | waits for 021: same enrich module, uses its entities |
| 023 | Chunking | done | solo | waits for 022: needs enriched stories; waits for 015: table/notice items |
| 024 | Embed and index into stores | done | solo | waits for 023: needs chunks |
| 025 | Retrieval test set and hybrid search script | done | solo | waits for 024: searches the stores |
| 026 | Embedding model bake-off | done | solo | waits for 025: needs the retrieval test |
| 027 | Old-pipeline baseline comparison | draft | solo | waits for 025: needs the retrieval test and search script |
| 028 | Customer-question answer check | draft | solo | waits for 025: needs the search script |
| 029 | Pipeline orchestrator and incremental ingest | draft | solo | waits for 024: drives every stage up to indexing |
| 030 | Quality report | draft | solo | waits for 029: reads all run stats; waits for 026, 027, 028: their scores |
| 031 | Full-corpus run and phase-1 sign-off | draft | solo | waits for 030: needs report; waits for 014: chosen grouping model; waits for 032: chosen text model |
| 032 | Text model Telugu bake-off | done | solo | waits for 017: needs canonical stories; waits for 006: model runner |
| 033 | Grouping retry for Gemma 4 (second sample, not resolution) | draft | solo | waits for 014: group_model is gemma4-31b |
