#!/usr/bin/env bash
# Runs ticket 011's eval checks (eval.md as changed 2026-11-10) after the folio stage has run.
cd /home/admin1/news-rag
export PATH=$HOME/.local/bin:$PATH
echo '$ E-1: uv run pytest -q tests/test_folio_parse.py'
uv run pytest -q tests/test_folio_parse.py 2>&1 | tail -1
echo
echo '$ E-2 step 2 (non-front dev-sample pages, those with a place)'
uv run python -c "import json;from newsrag.stages.registry import REGISTRY;from newsrag.stages.base import version_slug;v=version_slug(REGISTRY['folio'].version);ids=[l.strip() for l in open('data/dev-sample.txt') if l.strip() and not l.strip().endswith('_01')];d=[json.load(open(f'/home/admin1/raw/work/folio/{v}/{i}.json')) for i in ids];print(len(d), sum(1 for x in d if x['place_te']))"
echo
echo '$ E-3 step 2'
uv run newsrag editions build && uv run python -c "import yaml;y=yaml.safe_load(open('data/editions.yaml')) or {};print({k: len(v) for k, v in y.items()})"
echo '$ main pages with folio done, per year'
uv run newsrag db sql "select extract(year from p.pub_date)::int, count(*) from ledger.stage_runs s join ledger.pages p using (page_id) where s.stage='folio' and s.status='done' and p.section='main' group by 1 order by 1"
echo
echo '$ E-4 step 1'
uv run newsrag editions apply
echo '$ E-4 step 2'
uv run newsrag db sql "select count(*), count(*) filter (where 'low_confidence:unknown_edition' = any(flags)), count(*) filter (where cardinality(edition_names)>0), count(*) filter (where source='main' and not 'low_confidence:unknown_edition' = any(flags)) from ledger.page_editions"
echo '$ E-4 step 3'
uv run newsrag db sql "select count(*) from ledger.stage_runs where stage='folio' and status='done'"
echo '$ E-4 step 4'
uv run newsrag db sql "select count(*) from ledger.page_editions where source='main' and (cardinality(edition_names)>0 or cardinality(districts_current)>0 or cardinality(districts_at_print)>0)"
echo
echo '$ E-3 step 3: uv run newsrag editions report'
uv run newsrag editions report
echo
echo '$ page_editions by source and states'
uv run newsrag db sql "select source, states, count(*) from ledger.page_editions group by 1,2 order by 3 desc"
echo
echo '$ E-5'
uv run pytest -q tests/test_editions.py -k "multi_letter_gets_names_from_letters or all_and_fh_not_split_into_letters or partial_letters_flag or low_agreement_is_ambiguous or fewer_than_three_pages_is_ambiguous or date_mismatch_flag or hand_added_keys_kept or ambiguous_place_prefers_town or state_only_folio" 2>&1 | tail -1
echo '$ E-6'
uv run pytest -q tests/test_editions.py -k "letters_before_code_map or state_only_page or copies_entry_districts or report_lists_ambiguous" 2>&1 | tail -1
echo '$ E-7'
uv run pytest -q tests/test_editions.py -k "state_pages_do_not_feed_the_map or main_page_is_state_level or report_counts_state_pages" 2>&1 | tail -1
echo '$ ruff'
uv run ruff check . && uv run ruff format --check . | tail -1
echo '$ unit tests'
uv run pytest -q -m "not gpu and not data and not stores" 2>&1 | tail -1
echo '$ stores tests for 011'
uv run pytest -q -m stores tests/test_editions.py 2>&1 | tail -1
