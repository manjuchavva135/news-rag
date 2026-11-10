# Grouping model bake-off (ticket 014)

judge: gemma4-31b
pages: 5 (2024-12-02_BG_main_01, 2024-12-05_BGNOP_main_01, 2025-10-20_ALL_main_02, 2024-12-06_GNOCLT_main_16, 2024-12-08_BGNOPSVACLT_main_10)
judge_threshold: 0.8

| candidate | status | mean judge score | pages below judge_threshold | seconds/page | telugu_read | note |
|---|---|---|---|---|---|---|
| qwen3-vl-32b | done | 0.461 | 4 | 12.36 | 0.343 |  |
| qwen3-vl-30b-a3b | done | 0.300 | 4 | 6.86 | 0.394 |  |
| gemma3-27b | skipped | - | - | - | - | same family as the judge gemma4-31b (gemma) |

Per page (judge score as counted; `*` = see notes):

| page | qwen3-vl-32b | qwen3-vl-30b-a3b |
|---|---|---|
| 2024-12-02_BG_main_01 | 0.222 | 0.000 |
| 2024-12-05_BGNOP_main_01 | 1.000* | 1.000* |
| 2025-10-20_ALL_main_02 | 0.333 | 0.000 |
| 2024-12-06_GNOCLT_main_16 | 0.750 | 0.500 |
| 2024-12-08_BGNOPSVACLT_main_10 | 0.000 | 0.000 |

Notes:

- qwen3-vl-32b 2024-12-05_BGNOP_main_01: judge_skipped (null score counted as 1)
- qwen3-vl-30b-a3b 2024-12-05_BGNOP_main_01: judge_skipped (null score counted as 1)

winner: qwen3-vl-32b
