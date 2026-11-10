# Judge bake-off (013 E-3 follow-up, 2026-11-10)

Same 10 clean pairs as `../E-3-clean-pairs.log` (two articles the stored judge called `correct`,
distinct headlines). Read-only runs; nothing written to the ledger.

- `judge_bakeoff.py <model> <out> <pairs> [v1|v2]`: the FR-2 page judge on the stored grouping
  (control) and on the grouping with the pair merged. v2 lists every headline-role block of an
  article instead of only the first headline.
- `one_story_check.py <model> <out> <pairs>`: one text-only request per news article (headline
  boxes + full text): "one story or two or more?". Controls = every news article of the stored
  grouping; corruption = the merged article.

| Setup | Merges caught | Real articles flagged |
|---|---|---|
| Gemma 3 27B, page judge (v1) | 0/10 | 1 of 20 pair articles |
| Gemma 3 27B, page judge (v2, all headlines) | 1/10 | 1 of 20 pair articles |
| InternVL3.5-38B fp8, page judge (v1) | 0/10 | 2 of 20 pair articles |
| Gemma 4 31B, page judge (v1) | 6/10 | 3 of 20 pair articles |
| Gemma 3 27B, one-story check | 9/10 | 14 of 68 news articles |
| Gemma 4 31B, one-story check | 9/10 | 12 of 68 news articles |
| Gemma 4 31B, page judge OR one-story check | 10/10 | 23 of 68 news articles |

Gemma 3 and Gemma 4 agree on 11 of their one-story flags among the real articles; most of those
articles also carry ticket-012 geometry flags (`disconnected`, `overlaps_other`,
`headline_below_body`) or a non-`correct` verdict from another judge, so many are likely real
grouping errors rather than false alarms. Not checked by a person.

Profiles added to `models.yaml`: `gemma4-31b` (bf16, `max_soft_tokens` 1120) and
`internvl3.5-38b` (fp8 on load, 40 tiles via `--hf-overrides`; bf16 does not leave KV room).
