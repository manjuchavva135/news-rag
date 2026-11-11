# 020: Enrichment: places and districts

**Status:** done
**Mode:** solo
**Runs:** waits for 019: same enrich module; waits for 018: reference list

## Summary

District-wise questions ("mining leases in each district") need each story's places resolved to
districts, both the district at print time and today's district, because AP redrew its districts in
2022 (grill D-12, D-18, A-44). The edition's own district (from ticket 011) is kept separately from the
places the story talks about.

## Functional requirements

- **FR-1** - DateStage `enrich_places` (`depends_on=("enrich_core",)`, `version="<enrich_model>-places-p1"`), with the same input, model use, concurrency, request settings, retries and failure rules as ticket 019 (FR-1, FR-3; failure flag `low_confidence:enrich_failed`). Per story, the local LLM (prompt `src/newsrag/prompts/enrich_places_v1.md`, asking for the place name as written, without case endings where possible) returns `{"places":[{"name": str, "type": "district"|"mandal"|"city"|"village"|"constituency"|"state"|"other"}]}`. A name that is not a substring of the input (after NFC/whitespace normalisation) is dropped. Places are de-duplicated per story by (normalised name, type).
- **FR-2** - Resolution (types `district`, `mandal`, `city` only): first `newsrag.geo.resolve(name, on_date=print_date)` on the name as written; only if that finds nothing, strip one trailing Telugu case suffix (`లోని`, `లో`, `కు`, `కి`, `ను`, `నుంచి`, `నుండి`, `పై`, `తో`, `వద్ద`, `గా`) and any ZWJ/ZWNJ left at the end, and resolve again (ticket 018; tests use the real committed `data/geo` files and a fake LLM). First keep the matches whose `kind` fits the LLM type (district→district, mandal→mandal, city→town), if any. If exactly one remains, it is used; if several remain and they agree on both `districts_current` and `districts_at_date`, the first is used; otherwise the place keeps all candidates and the informational flag `place_ambiguous`. No match → informational flag `place_unresolved`. Type `state` maps Andhra Pradesh / ఆంధ్రప్రదేశ్ → `andhra_pradesh`, Telangana / తెలంగాణ → `telangana`, others → null. Types `village`, `constituency`, `other` are stored without resolution and without a flag. Each place record: `{name, llm_type, kind, state, districts_current: [...], districts_at_print: [...] (= resolver's districts_at_date), candidates?, flags}`. These flags are informational, not `low_confidence:`.
- **FR-3** - Story-level fields: `districts_current` and `districts_at_print` = sorted unique ids over its non-ambiguous places; `states` = sorted unique states of resolved places, of type `state` places, and of ambiguous places whose candidates all share one state. The edition's districts are not read here; ticket 023 adds them at chunk time from `ledger.page_editions`.
- **FR-4** - Its own artifact per date: `{pub_date, stories:{<story_id>:{places:[...], districts_current, districts_at_print, states, flags}}}`, merged by `newsrag.enrich.load()` (ticket 019 FR-6).
- **FR-5** - `uv run newsrag enrich stats --places [--date D ...]` prints `places_per_story=<x>`, then for each of `district`, `mandal`, `city`: `<type>: resolved=<pct> ambiguous=<pct> unresolved=<pct>` (ambiguous does not count as resolved), `combined_resolved=<pct>` (over the three types, weighted by count), and the top 10 unresolved names of those types (to grow `geo/aliases.yaml`).

## Non-functional requirements

- **NFR-1** - On the three test dates, ≥ 70% of extracted places of types district/mandal/city combined resolve (not ambiguous, not unresolved).

## Affected areas

- `src/newsrag/stages/enrich_places.py`, `src/newsrag/enrich/places.py`, `src/newsrag/prompts/enrich_places_v1.md` (new)
- `src/newsrag/stages/registry.py`, `src/newsrag/cli.py` (changed)
- `tests/test_enrich_places.py` (new)

## Don't touch

- `data/geo/*.yaml` except adding aliases to `src/newsrag/geo/aliases.yaml`.

## Out of scope

- Map views, constituency mapping.

## Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| D-1 | Districts stored | current and at print time | grill D-18 |
| D-2 | Story place vs edition district | stored separately | grill A-44 |
| D-3 | Unresolved places | kept, flagged | nothing silently dropped (grill D-20) |

## Open questions

None.
