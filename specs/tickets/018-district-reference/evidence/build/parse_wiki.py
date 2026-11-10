"""Parse the Wikipedia mandal tables into JSON rows {name, link, district_link, division}."""

import json
import re
import sys

LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def strip_refs(s):
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s)
    s = re.sub(r"<nowiki/>\w*", "", s)
    return s


def item(text):
    """(name, link) of one list item / cell."""
    text = strip_refs(text).strip()
    m = LINK.search(text)
    if m:
        link = m.group(1).strip()
        name = (m.group(2) or m.group(1)).strip()
    else:
        link = None
        name = re.sub(r"\s*\(.*?\)\s*$", "", text).strip()
    name = re.sub(r"\s+", " ", name)
    return name, link


def parse_modern(path):
    """AP 2025-08 / current: one row per mandal; rowspan cells for division / district."""
    rows = []
    division = district = None
    inside = False
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith('{| class="wikitable'):
            inside = True
            continue
        if line.startswith("|}"):
            inside = False
        if not inside or line.startswith("!"):
            continue
        if not line.startswith("|") or line.startswith("|-") or line.startswith("|}"):
            continue
        cell = re.sub(r'^\|\s*(rowspan="\d+"\s*\|)?', "", line).strip()
        if not cell:
            continue
        lm = LINK.search(cell)
        target = lm.group(1).strip().lower() if lm else cell.lower()
        low = target
        if "mandal" not in target and "district" in target:
            district = LINK.search(cell).group(1).strip()
            rows[-1]["district_link"] = district
            rows[-1]["division"] = division
        elif "division" in low and "mandal" not in low:
            division = LINK.search(cell).group(1).strip() if LINK.search(cell) else cell
            rows[-1]["division"] = division
        else:
            name, link = item(cell)
            rows.append({"name": name, "link": link, "district_link": district, "division": division})
    return rows


def parse_2022(path):
    """AP 2022-03: one table row per old district, a numbered list of its mandals."""
    rows = []
    district = None
    started = False
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("== Mandals"):
            started = True
        if not started:
            continue
        if line.startswith("==References"):
            break
        m = re.match(r"^\|\s*\[\[([^\]|]+)", line)
        if m:
            district = m.group(1).strip()
            continue
        if line.startswith("# "):
            body = re.sub(r"\s*\([A-Z]+\)\s*\[.*?\]\s*$", "", line[2:])
            name, link = item(body)
            rows.append({"name": name, "link": link, "district_link": district, "division": None})
    return rows


def parse_ts(path):
    """TS: a cell of '* name' items, then a district cell (rowspan covers later rows)."""
    rows = []
    pending = []
    district = None
    span_left = 0
    inside = False
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith('{| class="sortable'):
            inside = True
            continue
        if not inside:
            continue
        if line.startswith("*"):
            pending.append(item(line.lstrip("* ")))
        elif line.startswith("|-") or line.startswith("|}"):
            if pending:
                if span_left <= 0:
                    raise SystemExit(f"items without district: {pending}")
                for name, link in pending:
                    rows.append({"name": name, "link": link, "district_link": district})
                pending = []
                span_left -= 1
        elif line.startswith("|") and "[[" in line:
            m = re.match(r'^\|\s*(?:rowspan="(\d+)")?[^\[]*\[\[([^\]|]+)', line)
            district = m.group(2).strip()
            span_left = int(m.group(1) or 1)
    return rows


if __name__ == "__main__":
    kind, src, dst = sys.argv[1:]
    fn = {"modern": parse_modern, "2022": parse_2022, "ts": parse_ts}[kind]
    rows = fn(src)
    json.dump(rows, open(dst, "w"), ensure_ascii=False, indent=1)
    from collections import Counter

    c = Counter(r["district_link"] for r in rows)
    print(len(rows), len(c))
    for k, v in sorted(c.items()):
        print(f"  {v:3d} {k}")
