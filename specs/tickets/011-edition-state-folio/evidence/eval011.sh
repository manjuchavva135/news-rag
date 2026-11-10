#!/usr/bin/env bash
# Runs ticket 011's eval checks (after the folio stage has run) and prints the evidence.
cd /home/admin1/news-rag
export PATH=$HOME/.local/bin:$PATH
V=$(uv run python -c "from newsrag.stages.registry import REGISTRY;from newsrag.stages.base import version_slug;print(version_slug(REGISTRY['folio'].version))")
echo "folio version: $V"
echo '$ E-1: uv run pytest -q tests/test_folio_parse.py -v'
uv run pytest -q tests/test_folio_parse.py -v 2>&1 | grep -E "PASSED|FAILED|passed|failed"
echo
echo '$ E-2 step 2 (glob limited to the dev-sample page_ids under folio/<current version>/, FR-6)'
uv run python -c "
import json
ids=[l.strip() for l in open('data/dev-sample.txt') if l.strip()]
d=[json.load(open(f'/home/admin1/raw/work/folio/$V/{i}.json')) for i in ids]
print(len(d), sum(1 for x in d if x['place_te']))"
echo
echo '$ E-2 step 2 as written (glob over every folio artifact)'
uv run python -c "import json,glob;d=[json.load(open(f)) for f in glob.glob('/home/admin1/raw/work/folio/*/*.json')];print(len(d), sum(1 for x in d if x['place_te']))"
echo
echo '$ E-3: uv run newsrag editions build'
uv run newsrag editions build
for y in 2024 2025 2026; do
  echo "\$ E-3 step 2 for $y"
  uv run python -c "import yaml;y=yaml.safe_load(open('data/editions.yaml'));e=y.get($y);print(len(e), sum(v['status']=='ok' for v in e.values())) if e else print('no $y entries')"
done
echo
echo '$ E-4 step 1: uv run newsrag editions apply'
uv run newsrag editions apply
echo '$ E-4 step 2'
uv run newsrag db sql "select count(*), count(*) filter (where 'low_confidence:unknown_edition' = any(flags)), count(*) filter (where cardinality(edition_names)>0) from ledger.page_editions"
echo '$ E-4 step 3'
uv run newsrag db sql "select count(*) from ledger.stage_runs where stage='folio' and status='done'"
echo
echo '$ E-3 step 3: uv run newsrag editions report'
uv run newsrag editions report
echo
echo '$ sources'
uv run newsrag db sql "select source, count(*) from ledger.page_editions group by 1 order by 2 desc"
echo
echo '$ E-5'
uv run pytest -q tests/test_editions.py -v 2>&1 | grep -E "PASSED|FAILED|passed|failed"
echo '$ E-6'
uv run pytest -q tests/test_editions.py -k "letters_before_code_map or state_only_page or copies_entry_districts or report_lists_ambiguous" 2>&1 | tail -1
echo '$ ruff'
uv run ruff check . && uv run ruff format --check . | tail -1
