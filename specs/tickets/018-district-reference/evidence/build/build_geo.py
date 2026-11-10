"""Build data/geo/*.yaml for ticket 018 from the snapshots in this folder.

Inputs (snapshots fetched 2026-11-09):
- wd_ap.csv, wd_ts.csv: Wikidata mandals of AP (Q122987710) / TS (Q122987738) with dated P131.
- ap_old.json (enwiki rev 1075240456, 13 districts), ap_2022p.json (rev 1304848017, 26 districts),
  ap_cur.json (current, 28 districts), ts.json: parsed Wikipedia tables (parse_wiki.py).
- link_qids.json: Wikipedia link title -> Wikidata id.
- te_extra.json: Telugu names for mandals Wikidata has no Telugu label for (from te.wikipedia).
"""

import collections
import csv
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
OUT = Path(sys.argv[1])
AS_OF = "2026-11-09"

WD = "https://www.wikidata.org/wiki/"
SRC = {
    "wd_ap": "https://www.wikidata.org/wiki/Q122987710",
    "wd_ts": "https://www.wikidata.org/wiki/Q122987738",
    "en_ap_2014": "https://en.wikipedia.org/w/index.php?title=List_of_mandals_of_Andhra_Pradesh&oldid=1075240456",
    "en_ap_2022": "https://en.wikipedia.org/w/index.php?title=List_of_mandals_of_Andhra_Pradesh&oldid=1304848017",
    "en_ap_2025": "https://en.wikipedia.org/wiki/List_of_mandals_of_Andhra_Pradesh",
    "en_ap_districts": "https://en.wikipedia.org/wiki/List_of_districts_of_Andhra_Pradesh",
    "ap_portal": "https://www.ap.gov.in/",
    "ap_aponline_2015": "https://web.archive.org/web/20141224033355/http://www.aponline.gov.in/quick%20links/apfactfile/info%20on%20districts/chittoor.html",
    "ap_2022_hindu": "https://www.thehindu.com/news/national/andhra-pradesh/thirteen-new-districts-come-into-existence-in-andhra-pradesh/article65289032.ece",
    "ap_2025_eenadu": "https://www.eenadu.net/telugu-news/andhra-pradesh/ap-cabinet--approves-formation-of-two-new-districts/1799/125240498",
    "ts_abstract": "https://telangana.gov.in/wp-content/uploads/2023/05/Telangana-State-Statistical-Abstract-2022-1.pdf",
    "ts_newdistricts": "https://web.archive.org/web/20161011223505/http://newdistrictsformation.telangana.gov.in/uploads/gos-circulars/1476130220055231.Warangal%20U.pdf",
    "en_ts": "https://en.wikipedia.org/wiki/List_of_mandals_in_Telangana",
    "te_wiki": "https://te.wikipedia.org/",
}

# --- districts -------------------------------------------------------------------------------
# id: (name_en, name_te, wikidata/wikipedia labels that mean this district)
AP_DISTRICTS = {
    "alluri_sitharama_raju": ("Alluri Sitharama Raju", "అల్లూరి సీతారామరాజు", ["Alluri Sitharama Raju district"]),
    "anakapalli": ("Anakapalli", "అనకాపల్లి", ["Anakapalli district"]),
    "anantapuramu": ("Anantapuramu", "అనంతపురం", ["Anantapuramu district", "Anantapur district"]),
    "annamayya": ("Annamayya", "అన్నమయ్య", ["Annamayya district"]),
    "bapatla": ("Bapatla", "బాపట్ల", ["Bapatla district", "Bapatla  district"]),
    "chittoor": ("Chittoor", "చిత్తూరు", ["Chittoor district", "Chittoor Urban district"]),
    "dr_b_r_ambedkar_konaseema": ("Dr. B. R. Ambedkar Konaseema", "డాక్టర్ బి.ఆర్. అంబేద్కర్ కోనసీమ", ["Dr. B.R. Ambedkar Konaseema district", "Konaseema district"]),
    "east_godavari": ("East Godavari", "తూర్పు గోదావరి", ["East Godavari district"]),
    "eluru": ("Eluru", "ఏలూరు", ["Eluru district", "Eluru  district"]),
    "guntur": ("Guntur", "గుంటూరు", ["Guntur district", "Guntur  district"]),
    "kakinada": ("Kakinada", "కాకినాడ", ["Kakinada district"]),
    "krishna": ("Krishna", "కృష్ణా", ["Krishna district", "Krishna  district"]),
    "kurnool": ("Kurnool", "కర్నూలు", ["Kurnool district", "Kurnool  district"]),
    "markapuram": ("Markapuram", "మార్కాపురం", ["Markapuram district"]),
    "nandyal": ("Nandyal", "నంద్యాల", ["Nandyal district", "Nandyal  district"]),
    "ntr": ("NTR", "ఎన్టీఆర్", ["NTR district", "NTR  district"]),
    "palnadu": ("Palnadu", "పల్నాడు", ["Palnadu district", "Palnadu  district"]),
    "parvathipuram_manyam": ("Parvathipuram Manyam", "పార్వతీపురం మన్యం", ["Parvathipuram Manyam district"]),
    "polavaram": ("Polavaram", "పోలవరం", ["Polavaram district"]),
    "prakasam": ("Prakasam", "ప్రకాశం", ["Prakasam district"]),
    "sri_potti_sriramulu_nellore": ("Sri Potti Sriramulu Nellore", "శ్రీ పొట్టి శ్రీరాములు నెల్లూరు", ["Sri Potti Sri Ramulu Nellore district", "Sri Potti Sri Ramulu Nellore District", "Nellore district"]),
    "sri_sathya_sai": ("Sri Sathya Sai", "శ్రీ సత్యసాయి", ["Sri Sathya Sai district"]),
    "srikakulam": ("Srikakulam", "శ్రీకాకుళం", ["Srikakulam district"]),
    "tirupati": ("Tirupati", "తిరుపతి", ["Tirupati district"]),
    "visakhapatnam": ("Visakhapatnam", "విశాఖపట్నం", ["Visakhapatnam district"]),
    "vizianagaram": ("Vizianagaram", "విజయనగరం", ["Vizianagaram district"]),
    "west_godavari": ("West Godavari", "పశ్చిమ గోదావరి", ["West Godavari district"]),
    "ysr_kadapa": ("YSR Kadapa", "వైఎస్ఆర్ కడప", ["YSR  Kadapa district", "YSR Kadapa district", "YSR  district", "Kadapa district"]),
}
TS_DISTRICTS = {
    "adilabad": ("Adilabad", "ఆదిలాబాద్", ["Adilabad district"]),
    "bhadradri_kothagudem": ("Bhadradri Kothagudem", "భద్రాద్రి కొత్తగూడెం", ["Bhadradri Kothagudem district", "Bhadradri district"]),
    "hanumakonda": ("Hanumakonda", "హనుమకొండ", ["Hanamkonda district", "Warangal (urban) district", "Warangal Urban District"]),
    "hyderabad": ("Hyderabad", "హైదరాబాద్", ["Hyderabad district", "Hyderabad district, India"]),
    "jagtial": ("Jagtial", "జగిత్యాల", ["Jagtial district"]),
    "jangaon": ("Jangaon", "జనగామ", ["Jangaon district"]),
    "jayashankar_bhupalpally": ("Jayashankar Bhupalpally", "జయశంకర్ భూపాలపల్లి", ["Jayashankar Bhupalpally district", "Jayashankar district"]),
    "jogulamba_gadwal": ("Jogulamba Gadwal", "జోగులాంబ గద్వాల", ["Jogulamba Gadwal district"]),
    "kamareddy": ("Kamareddy", "కామారెడ్డి", ["Kamareddy district"]),
    "karimnagar": ("Karimnagar", "కరీంనగర్", ["Karimnagar district"]),
    "khammam": ("Khammam", "ఖమ్మం", ["Khammam district"]),
    "kumuram_bheem_asifabad": ("Kumuram Bheem Asifabad", "కుమురం భీం ఆసిఫాబాద్", ["Kumaram Bheem Asifabad district", "Komaram Bheem Asifabad district"]),
    "mahabubabad": ("Mahabubabad", "మహబూబాబాద్", ["Mahabubabad district"]),
    "mahabubnagar": ("Mahabubnagar", "మహబూబ్‌నగర్", ["Mahabubnagar district"]),
    "mancherial": ("Mancherial", "మంచిర్యాల", ["Mancherial district"]),
    "medak": ("Medak", "మెదక్", ["Medak district"]),
    "medchal_malkajgiri": ("Medchal–Malkajgiri", "మేడ్చల్ మల్కాజిగిరి", ["Medchal-Malkajgiri district", "Medchal–Malkajgiri district"]),
    "mulugu": ("Mulugu", "ములుగు", ["Mulugu district"]),
    "nagarkurnool": ("Nagarkurnool", "నాగర్‌కర్నూల్", ["Nagarkurnool district"]),
    "nalgonda": ("Nalgonda", "నల్గొండ", ["Nalgonda district"]),
    "narayanpet": ("Narayanpet", "నారాయణపేట", ["Narayanpet district"]),
    "nirmal": ("Nirmal", "నిర్మల్", ["Nirmal district"]),
    "nizamabad": ("Nizamabad", "నిజామాబాద్", ["Nizamabad district"]),
    "peddapalli": ("Peddapalli", "పెద్దపల్లి", ["Peddapalli district"]),
    "rajanna_sircilla": ("Rajanna Sircilla", "రాజన్న సిరిసిల్ల", ["Rajanna Sircilla district"]),
    "ranga_reddy": ("Ranga Reddy", "రంగారెడ్డి", ["Ranga Reddy district"]),
    "sangareddy": ("Sangareddy", "సంగారెడ్డి", ["Sangareddy district"]),
    "siddipet": ("Siddipet", "సిద్దిపేట", ["Siddipet district"]),
    "suryapet": ("Suryapet", "సూర్యాపేట", ["Suryapet district"]),
    "vikarabad": ("Vikarabad", "వికారాబాద్", ["Vikarabad district"]),
    "wanaparthy": ("Wanaparthy", "వనపర్తి", ["Wanaparthy district"]),
    "warangal": ("Warangal", "వరంగల్", ["Warangal district", "Warangal (rural) district", "Warangal Rural District"]),
    "yadadri_bhuvanagiri": ("Yadadri Bhuvanagiri", "యాదాద్రి భువనగిరి", ["Yadadri Bhuvanagiri district"]),
}
LABEL2ID = {}
for table in (AP_DISTRICTS, TS_DISTRICTS):
    for did, (_, _, labels) in table.items():
        for lab in labels:
            LABEL2ID[lab] = did

# Towns: id -> (name_en, name_te)
TOWNS = {
    "paderu": ("Paderu", "పాడేరు"), "anakapalli": ("Anakapalli", "అనకాపల్లి"),
    "anantapuramu": ("Anantapuramu", "అనంతపురం"), "rayachoti": ("Rayachoti", "రాయచోటి"),
    "madanapalle": ("Madanapalle", "మదనపల్లె"), "bapatla": ("Bapatla", "బాపట్ల"),
    "chittoor": ("Chittoor", "చిత్తూరు"), "amalapuram": ("Amalapuram", "అమలాపురం"),
    "kakinada": ("Kakinada", "కాకినాడ"), "rajamahendravaram": ("Rajamahendravaram", "రాజమహేంద్రవరం"),
    "eluru": ("Eluru", "ఏలూరు"), "guntur": ("Guntur", "గుంటూరు"),
    "machilipatnam": ("Machilipatnam", "మచిలీపట్నం"), "kurnool": ("Kurnool", "కర్నూలు"),
    "markapuram": ("Markapuram", "మార్కాపురం"), "nandyal": ("Nandyal", "నంద్యాల"),
    "vijayawada": ("Vijayawada", "విజయవాడ"), "narasaraopet": ("Narasaraopet", "నరసరావుపేట"),
    "parvathipuram": ("Parvathipuram", "పార్వతీపురం"), "rampachodavaram": ("Rampachodavaram", "రంపచోడవరం"),
    "ongole": ("Ongole", "ఒంగోలు"), "nellore": ("Nellore", "నెల్లూరు"),
    "puttaparthi": ("Puttaparthi", "పుట్టపర్తి"), "srikakulam": ("Srikakulam", "శ్రీకాకుళం"),
    "tirupati": ("Tirupati", "తిరుపతి"), "visakhapatnam": ("Visakhapatnam", "విశాఖపట్నం"),
    "vizianagaram": ("Vizianagaram", "విజయనగరం"), "bhimavaram": ("Bhimavaram", "భీమవరం"),
    "kadapa": ("Kadapa", "కడప"),
    # Telangana
    "adilabad": ("Adilabad", "ఆదిలాబాద్"), "kothagudem": ("Kothagudem", "కొత్తగూడెం"),
    "hanumakonda": ("Hanumakonda", "హనుమకొండ"), "hyderabad": ("Hyderabad", "హైదరాబాద్"),
    "jagtial": ("Jagtial", "జగిత్యాల"), "jangaon": ("Jangaon", "జనగామ"),
    "bhupalpally": ("Bhupalpally", "భూపాలపల్లి"), "gadwal": ("Gadwal", "గద్వాల"),
    "kamareddy": ("Kamareddy", "కామారెడ్డి"), "karimnagar": ("Karimnagar", "కరీంనగర్"),
    "khammam": ("Khammam", "ఖమ్మం"), "asifabad": ("Asifabad", "ఆసిఫాబాద్"),
    "mahabubabad": ("Mahabubabad", "మహబూబాబాద్"), "mahabubnagar": ("Mahabubnagar", "మహబూబ్‌నగర్"),
    "mancherial": ("Mancherial", "మంచిర్యాల"), "medak": ("Medak", "మెదక్"),
    "shamirpet": ("Shamirpet", "షామీర్‌పేట"), "mulugu": ("Mulugu", "ములుగు"),
    "nagarkurnool": ("Nagarkurnool", "నాగర్‌కర్నూల్"), "nalgonda": ("Nalgonda", "నల్గొండ"),
    "narayanpet": ("Narayanpet", "నారాయణపేట"), "nirmal": ("Nirmal", "నిర్మల్"),
    "nizamabad": ("Nizamabad", "నిజామాబాద్"), "peddapalli": ("Peddapalli", "పెద్దపల్లి"),
    "sircilla": ("Sircilla", "సిరిసిల్ల"), "shamshabad": ("Shamshabad", "శంషాబాద్"),
    "sangareddy": ("Sangareddy", "సంగారెడ్డి"), "siddipet": ("Siddipet", "సిద్దిపేట"),
    "suryapet": ("Suryapet", "సూర్యాపేట"), "vikarabad": ("Vikarabad", "వికారాబాద్"),
    "wanaparthy": ("Wanaparthy", "వనపర్తి"), "warangal": ("Warangal", "వరంగల్"),
    "bhuvanagiri": ("Bhuvanagiri", "భువనగిరి"),
}

AP_2014 = {
    "anantapuramu": "anantapuramu", "chittoor": "chittoor", "east_godavari": "kakinada",
    "guntur": "guntur", "ysr_kadapa": "kadapa", "krishna": "machilipatnam", "kurnool": "kurnool",
    "sri_potti_sriramulu_nellore": "nellore", "prakasam": "ongole", "srikakulam": "srikakulam",
    "visakhapatnam": "visakhapatnam", "vizianagaram": "vizianagaram", "west_godavari": "eluru",
}
AP_2022 = {
    "alluri_sitharama_raju": "paderu", "anakapalli": "anakapalli", "anantapuramu": "anantapuramu",
    "annamayya": "rayachoti", "bapatla": "bapatla", "chittoor": "chittoor",
    "dr_b_r_ambedkar_konaseema": "amalapuram", "east_godavari": "rajamahendravaram",
    "eluru": "eluru", "guntur": "guntur", "kakinada": "kakinada", "krishna": "machilipatnam",
    "kurnool": "kurnool", "nandyal": "nandyal", "ntr": "vijayawada", "palnadu": "narasaraopet",
    "parvathipuram_manyam": "parvathipuram", "prakasam": "ongole",
    "sri_potti_sriramulu_nellore": "nellore", "sri_sathya_sai": "puttaparthi",
    "srikakulam": "srikakulam", "tirupati": "tirupati", "visakhapatnam": "visakhapatnam",
    "vizianagaram": "vizianagaram", "west_godavari": "bhimavaram", "ysr_kadapa": "kadapa",
}
AP_2025 = dict(AP_2022, annamayya="madanapalle", markapuram="markapuram", polavaram="rampachodavaram")
TS_2019 = {
    "adilabad": "adilabad", "bhadradri_kothagudem": "kothagudem", "hanumakonda": "hanumakonda",
    "hyderabad": "hyderabad", "jagtial": "jagtial", "jangaon": "jangaon",
    "jayashankar_bhupalpally": "bhupalpally", "jogulamba_gadwal": "gadwal",
    "kamareddy": "kamareddy", "karimnagar": "karimnagar", "khammam": "khammam",
    "kumuram_bheem_asifabad": "asifabad", "mahabubabad": "mahabubabad",
    "mahabubnagar": "mahabubnagar", "mancherial": "mancherial", "medak": "medak",
    "medchal_malkajgiri": "shamirpet", "mulugu": "mulugu", "nagarkurnool": "nagarkurnool",
    "nalgonda": "nalgonda", "narayanpet": "narayanpet", "nirmal": "nirmal",
    "nizamabad": "nizamabad", "peddapalli": "peddapalli", "rajanna_sircilla": "sircilla",
    "ranga_reddy": "shamshabad", "sangareddy": "sangareddy", "siddipet": "siddipet",
    "suryapet": "suryapet", "vikarabad": "vikarabad", "wanaparthy": "wanaparthy",
    "warangal": "warangal", "yadadri_bhuvanagiri": "bhuvanagiri",
}

PERIODS = {
    "andhra_pradesh": [
        ("ap_districts_2014_2022.yaml", "2014-06-02", "2022-04-03", AP_2014, "2020-01-01", "ap_old.json"),
        ("ap_districts_2022.yaml", "2022-04-04", "2025-12-30", AP_2022, "2025-06-01", "ap_2022p.json"),
        ("ap_districts_2025.yaml", "2025-12-31", None, AP_2025, "2026-06-01", None),
    ],
    "telangana": [
        ("ts_districts_2019.yaml", "2019-02-17", None, TS_2019, "2026-06-01", None),
    ],
}
FILE_SOURCES = {
    "ap_districts_2014_2022.yaml": ["ap_portal", "ap_aponline_2015", "en_ap_2014", "wd_ap"],
    "ap_districts_2022.yaml": ["ap_portal", "ap_2022_hindu", "en_ap_2022", "en_ap_districts", "wd_ap"],
    "ap_districts_2025.yaml": ["ap_portal", "ap_2025_eenadu", "en_ap_districts", "en_ap_2025", "wd_ap"],
    "ts_districts_2019.yaml": ["ts_abstract", "ts_newdistricts", "en_ts", "wd_ts"],
}

# HQ town -> its mandal: Wikidata/English name of the mandal (looked up in the town's district).
HQ_MANDAL_OVERRIDE = {
    "vijayawada": "vijayawadacentral", "visakhapatnam": "maharanipeta",
    "anakapalli": "anakapalle", "markapuram": "markapur", "nandyal": "nandyalurban",
    "vizianagaram": "vizianagaramurban", "adilabad": "adilabadurban", "siddipet": "siddipetaurban",
    "suryapet": "suryapeta",
    "hyderabad": "nampally", "rajamahendravaram": "rajamahendravaramurban",
    "anantapuramu": "anantapururban", "kadapa": "kadapa", "nellore": "nelloreurban",
    "guntur": "gunturwest", "kurnool": "kurnoolurban", "tirupati": "tirupatiurban",
    "kakinada": "kakinadaurban", "eluru": "eluruurban", "ongole": "ongoleurban",
    "hanumakonda": "hanamkonda", "warangal": "warangal", "karimnagar": "karimnagar",
    "khammam": "khammamurban", "nizamabad": "nizamabadnorth", "mahabubnagar": "mahabubnagarurban",
    "kothagudem": "kothagudem", "bhupalpally": "bhupalapally", "gadwal": "gadwal",
    "shamirpet": "shamirpet", "shamshabad": "shamshabad", "bhuvanagiri": "bhongir",
    "machilipatnam": "machilipatnamnorth", "asifabad": "asifabad", "sircilla": "sircilla",
    "chittoor": "chittoorurban",
}


def norm(s):
    s = s.lower()
    s = re.sub(r",.*$", "", s)
    s = re.sub(r"\b(mandal|mandalam)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


def slug(s):
    s = s.lower().replace("–", " ").replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def strip_suffix_en(s):
    return re.sub(r"\s+mandal$", "", s.strip(), flags=re.I).strip()


def strip_suffix_te(s):
    s = s.strip()
    for suf in ("మండలం", "మండల్"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s


def load_wd(path):
    wd = collections.defaultdict(list)
    for x in csv.DictReader(open(HERE / path, encoding="utf-8")):
        wd[x["m"]].append(x)
    return {m: xs for m, xs in wd.items() if not xs[0]["dis"]}


def wd_at(xs, day):
    cands = []
    for x in xs:
        if not x["d"] or x["den"] not in LABEL2ID:
            continue
        s = x["start"][:10] or "0000"
        e = x["end"][:10] or "9999"
        if s <= day <= e:
            dated = bool(x["start"] or x["end"])
            pref = x["rank"] == "PreferredRank"
            cands.append((dated, pref, LABEL2ID[x["den"]]))
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][2]


def build_state(state, wdf, te_extra, qids, log):
    wd = load_wd(wdf)
    periods = PERIODS[state]
    # base list
    mandals = {}
    for m, xs in wd.items():
        en = strip_suffix_en(xs[0]["en"])
        en = re.sub(r"\s*\(\d+\)\s*", " ", en).strip()
        te = strip_suffix_te(xs[0]["te"]) if xs[0]["te"] else te_extra.get(m)
        mandals[m] = {"qid": m, "name_en": en, "name_te": te, "districts": {}}
    byname = collections.defaultdict(set)
    for m, d in mandals.items():
        byname[norm(d["name_en"])].add(m)
    for fname, _vf, _vt, dmap, day, table in periods:
        valid = set(dmap)
        tab = {}
        if table:
            for r in json.load(open(HERE / table, encoding="utf-8")):
                did = LABEL2ID.get(r["district_link"])
                if did is None:
                    raise SystemExit(f"{table}: unknown district {r['district_link']!r}")
                q = qids.get(r["link"]) if r["link"] else None
                if q in mandals:
                    tab[q] = did
                elif len(byname.get(norm(r["name"]), ())) == 1:
                    (q,) = byname[norm(r["name"])]
                    tab.setdefault(q, did)
                else:
                    # ambiguous name: the candidate that Wikidata puts in this district
                    cands = [c for c in byname.get(norm(r["name"]), ()) if wd_at(wd[c], day) == did]
                    if len(cands) == 1:
                        tab.setdefault(cands[0], did)
        for m, d in mandals.items():
            w = wd_at(wd[m], day)
            t = tab.get(m)
            if t is not None and w is not None and t != w and w in valid:
                log.append(f"{fname}: {d['name_en']}: table {t} vs wikidata {w} -> wikidata")
            got = w if w in valid else t
            d["districts"][fname] = got
    # infer earlier periods from later ones where still unknown (district made from one old one)
    for i in range(len(periods) - 2, -1, -1):
        fname = periods[i][0]
        nxt = periods[i + 1][0]
        parent = collections.defaultdict(collections.Counter)
        for d in mandals.values():
            if d["districts"][fname] and d["districts"][nxt]:
                parent[d["districts"][nxt]][d["districts"][fname]] += 1
        for d in mandals.values():
            if d["districts"][fname] is None and d["districts"][nxt]:
                guess = parent[d["districts"][nxt]].most_common(1)
                if guess:
                    d["districts"][fname] = guess[0][0]
                    log.append(f"{fname}: {d['name_en']}: inferred {guess[0][0]} from {d['districts'][nxt]}")
    # current district must exist; drop mandals with none
    cur = periods[-1][0]
    for m in [m for m, d in mandals.items() if d["districts"][cur] is None]:
        log.append(f"dropped (no current district): {mandals[m]['name_en']} {m}")
        del mandals[m]
    return mandals


def main():
    qids = json.load(open(HERE / "link_qids.json"))
    te_extra = json.load(open(HERE / "te_extra.json", encoding="utf-8"))
    log = []
    all_mandals = {}
    for state, wdf in (("andhra_pradesh", "wd_ap.csv"), ("telangana", "wd_ts.csv")):
        ms = build_state(state, wdf, te_extra, qids, log)
        for d in ms.values():
            d["state"] = state
        all_mandals.update(ms)
    # ids: slug of the English name; clash -> add current district id
    cnt = collections.Counter(slug(d["name_en"]) for d in all_mandals.values())
    for d in all_mandals.values():
        s = slug(d["name_en"])
        cur = list(d["districts"].values())[-1]
        d["id"] = s if cnt[s] == 1 else f"{s}_{cur}"
    ids = collections.Counter(d["id"] for d in all_mandals.values())
    dup = [i for i, n in ids.items() if n > 1]
    for d in all_mandals.values():
        if d["id"] in dup:
            d["id"] = f"{d['id']}_{d['qid'].lower()}"
    missing_te = [d for d in all_mandals.values() if not d["name_te"]]
    if missing_te:
        raise SystemExit(f"no Telugu name: {[(d['qid'], d['name_en']) for d in missing_te]}")

    # district files
    OUT.mkdir(parents=True, exist_ok=True)
    periods_yaml = {}
    for state, plist in PERIODS.items():
        table = AP_DISTRICTS if state == "andhra_pradesh" else TS_DISTRICTS
        periods_yaml[state] = []
        for fname, vf, vt, dmap, _day, _t in plist:
            periods_yaml[state].append({"file": fname, "valid_from": vf, "valid_to": vt})
            ms = [d for d in all_mandals.values() if d["state"] == state]
            districts = []
            for did in sorted(dmap):
                town = dmap[did]
                in_d = [d for d in ms if d["districts"][fname] == did]
                want = HQ_MANDAL_OVERRIDE.get(town, norm(TOWNS[town][0]))
                hit = [d for d in in_d if norm(d["name_en"]) == want]
                if not hit:
                    hit = [d for d in in_d if norm(d["name_en"]).startswith(norm(TOWNS[town][0])[:5])]
                    log.append(f"{fname}: HQ {town}: no mandal {want!r}; candidates {[d['name_en'] for d in hit]}")
                    if not hit:
                        raise SystemExit(f"{fname}: no HQ mandal for {town} in {did}: {[d['name_en'] for d in in_d]}")
                name_en, name_te, _ = table[did]
                districts.append({
                    "id": did, "name_en": name_en, "name_te": name_te,
                    "hq": {"id": town, "name_en": TOWNS[town][0], "name_te": TOWNS[town][1], "mandal_id": hit[0]["id"]},
                })
            doc = {"as_of": AS_OF, "source_urls": [SRC[k] for k in FILE_SOURCES[fname]], "districts": districts}
            write(OUT / fname, doc, flow_keys=("source_urls",))
    write(OUT / "periods.yaml", periods_yaml)
    files_by_state = {s: [p[0] for p in pl] for s, pl in PERIODS.items()}
    mandal_list = []
    for d in sorted(all_mandals.values(), key=lambda d: (d["state"], d["id"])):
        mandal_list.append({
            "id": d["id"], "name_en": d["name_en"], "name_te": d["name_te"], "state": d["state"],
            "districts": {f: d["districts"][f] for f in files_by_state[d["state"]]},
            "wikidata": d["qid"],
        })
    doc = {
        "as_of": AS_OF,
        "source_urls": [SRC[k] for k in ("wd_ap", "wd_ts", "en_ap_2014", "en_ap_2022", "en_ap_2025", "ts_abstract", "en_ts", "te_wiki")],
        "mandals": mandal_list,
    }
    write(OUT / "mandals.yaml", doc, flow_keys=("source_urls", "districts"))
    (HERE / "build_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    c = collections.Counter(d["state"] for d in all_mandals.values())
    print(dict(c), "log lines", len(log))


class Dumper(yaml.SafeDumper):
    pass


def write(path, doc, flow_keys=()):
    text = yaml.dump(doc, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=200)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
