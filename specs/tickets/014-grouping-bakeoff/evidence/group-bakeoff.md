# Grouping model bake-off (ticket 014)

judge: gemma4-31b
pages: 5 (2024-12-02_BG_main_01, 2024-12-05_BGNOP_main_01, 2025-10-20_ALL_main_02, 2024-12-06_GNOCLT_main_16, 2024-12-08_BGNOPSVACLT_main_10)
judge_threshold: 0.8

| candidate | status | mean judge score | pages below judge_threshold | seconds/page | telugu_read | note |
|---|---|---|---|---|---|---|
| qwen3-vl-32b | done | 0.461 | 4 | 12.36 | 0.343 |  |
| qwen3-vl-30b-a3b | done | 0.300 | 4 | 6.86 | 0.394 |  |
| gemma3-27b | done | 0.356 | 5 | 12.41 | 0.662 |  |
| gemma4-31b | done | 0.865 | 1 | 30.76 | 0.848 |  |

Per page (judge score as counted; `*` = see notes):

| page | qwen3-vl-32b | qwen3-vl-30b-a3b | gemma3-27b | gemma4-31b |
|---|---|---|---|---|
| 2024-12-02_BG_main_01 | 0.222 | 0.000 | 0.371 | 0.938 |
| 2024-12-05_BGNOP_main_01 | 1.000* | 1.000* | 0.400 | 1.000* |
| 2025-10-20_ALL_main_02 | 0.333 | 0.000 | 0.560 | 0.818 |
| 2024-12-06_GNOCLT_main_16 | 0.750 | 0.500 | 0.333 | 1.000 |
| 2024-12-08_BGNOPSVACLT_main_10 | 0.000 | 0.000 | 0.114 | 0.571 |

Notes:

- qwen3-vl-32b 2024-12-05_BGNOP_main_01: judge_skipped (null score counted as 1)
- qwen3-vl-30b-a3b 2024-12-05_BGNOP_main_01: judge_skipped (null score counted as 1)
- gemma4-31b 2024-12-05_BGNOP_main_01: judge_skipped (null score counted as 1)

winner: gemma4-31b
