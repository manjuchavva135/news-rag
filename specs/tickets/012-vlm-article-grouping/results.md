# 012: VLM article grouping - Results

Branch: ticket-012-grouping (the router asked for this name; the ticket convention is `ticket/012-vlm-article-grouping`)
Worktree: /home/admin1/news-rag/.claude/worktrees/agent-a5ca9232307937aae
Port: 8701 (not used, no web app)

## Checks
| Check | Result | Evidence |
|-------|--------|----------|
| ruff | PASS | evidence/ruff.log |
| unit pytest | PASS (386 passed) | evidence/pytest-unit.log |
| E-1 | PASS | evidence/E-1.log |
| E-2 | FAIL on NFR-2 only; NFR-1 PASS, 0 failed pages | evidence/E-2-*.log |
| E-3 | PASS | evidence/E-3-<page>.png (3 files) |
| E-4 | PASS | evidence/E-4.log |
| E-5 | PASS | evidence/E-5.log |
| E-6 | PASS | evidence/E-6.log |
| E-7 | FAIL (model grouping differs from the expected one) | evidence/E-7.log, E-7-page-all.log |

NFR-1: `pages=20 failed=0 text_blocks=784 unassigned=22 pct=2.8` (limit 5%).
NFR-2: `group: 20 pages in 271.7 s (excl. model load) = 0.07 pages/s` (needs 0.2). 20 requests, 272,625 prompt tokens (13.6k per page), 14,295 completion tokens.

## Attempts
- Attempt 1: first live run failed the 009 page: the model used `masthead` (a kind) as a role, three replies in a row. Fixed by sending the reply JSON schema as `response_format` (known kinds/roles) and clarifying roles in the prompt.
- Attempt 2: pages 2-4 of prompt tuning for E-7 (headline over several boxes, duplicate boxes, "ends in .." hints): no change in the model's grouping of the 009 page.
- Attempt 3: throughput 0.03 pages/s. Causes found: pretty-printed JSON (~950 output tokens for 47 blocks) and runaway replies. Fixed with a compact one-line JSON instruction (~600 tokens/page), schema caps (`maxItems` = block count, `minItems: 1`), `unassigned` not required in the schema (when it was required the model looped on whitespace until max_tokens) and `stop=["\n\n"]`. Result 0.07-0.08 pages/s, 0 failures.
- Attempt 4: tried a new fp8 (on-the-fly quantised) profile of the same model: 0.10 pages/s, KV cache 3x bigger; still far below 0.2. Reverted (not kept in models.yaml).
- Attempt 5 (final): all checks above; E-2 NFR-2 and E-7 still fail.

## Why NFR-2 is missed
At the ticket's defaults (image long side 3584, 80-char text list) a request has ~13.6k prompt tokens. Prefill of those costs ~5 s/page and the KV cache (76k tokens for bf16 on this GPU) holds only ~5 requests, so decoding (~20 tok/s per stream, weight-bound) is barely batched. Measured on the 009 page: image 3584 px: 11.8k prompt tokens, 48 s; 1536 px: 5.7k tokens, 46 s (single request, pretty JSON). Reaching 0.2 pages/s needs a smaller image/text list and/or a faster model, which is a decision for the router / ticket 014.

## Why E-7 fails
The model (qwen3-vl-32b, temperature 0) splits story 2 (headline "మాయ 'లేడి'.. జగత్ కిలాడి!" in two boxes, text in 3 columns) into separate articles and does not attach the overlapping duplicate headline box b012 to story 1 (it joins b012 with the third column b011). Story 1 itself is right (headline, kicker, b007 then b008, photo b006). The geometry side works on the real data (unit test `test_009_page_with_the_ids_of_the_ocr_final_artifact`). The ticket's ids in E-6/E-7 differ from the real ocr_final ids: ticket b012 = real b007, ticket b032 = real b012, ticket b007 = real b016.

## Changes
- `src/newsrag/stages/group.py` - stage `group`, transport/retry/split logic, throughput line
- `src/newsrag/grouping/{input,parse,geometry,overlay,cli}.py` - model input, schema check, geometry rules, overlay, `group overlay|stats`
- `src/newsrag/prompts/group_v1.md` - prompt
- `src/newsrag/config.py` - `# --- group ---` settings; `pipeline.yaml` - `group_model` via set_pipeline_key
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` - registration
- `tests/test_grouping_{parse,geometry,live}.py`

## Notes for the router
Decisions the ticket did not settle:
- Reply schema sent as `response_format` + `stop=["\n\n"]`; `unassigned` optional in the schema (parser defaults it to []). Prompt asks for compact one-line JSON. Without these the model looped to max_tokens (400 s) or failed on kind/role mixups.
- Block number `n` = the block's `order`; ordering ties use `order`.
- `disconnected` = graph connectivity at `group_max_gap_px`; `overlaps_other` flags the article whose union box covers another article's block.
- Wide blocks (FR-9b) count x-overlap only above `group_order_min_overlap_px` (30); "wide" is found first by overlapping two mutually disjoint blocks, then by touching two different columns.
- FR-10 "same role group" = the six text roles together; duplicates are listed after the other blocks of their own role.
- Artifact blocks carry only `block_id`, `role` (+ `duplicate_of`); `draw_articles` takes an optional `blocks` kwarg, else reads ocr_final from the ledger.
- Page flag `split_halves` (informational) when the half-page split was used.
- Image sent as JPEG q92, only the half's boxes drawn on a split; max_tokens 8192 (FR-8) over 4096 (FR-1).
- Request timeout 300 s includes time waiting in the vLLM queue; with 16 threads and ~5 concurrent slots this is close to the limit on slow pages.
- `models.yaml` unchanged (fp8 profile tried and reverted).
