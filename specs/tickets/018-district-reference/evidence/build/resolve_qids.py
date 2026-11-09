import json, sys, time, urllib.parse, urllib.request
UA = "newsrag-geo/0.1 (https://github.com/; research script) python-urllib"
titles = set()
for f in ["ap_cur.json", "ap_2022p.json", "ap_old.json", "ts.json"]:
    for r in json.load(open(f)):
        if r["link"]:
            titles.add(r["link"])
titles = sorted(titles)
out = {}
def opener(req):
    for k in range(6):
        try:
            return urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            if e.code != 429: raise
            time.sleep(10 * (k + 1))
    raise SystemExit("429")
import urllib.error
for i in range(0, len(titles), 50):
    batch = titles[i:i + 50]
    q = urllib.parse.urlencode({"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                                "redirects": 1, "format": "json", "titles": "|".join(batch)})
    req = urllib.request.Request("https://en.wikipedia.org/w/api.php?" + q, headers={"User-Agent": UA})
    d = json.load(opener(req))["query"]
    norm = {x["from"]: x["to"] for x in d.get("normalized", [])}
    red = {x["from"]: x["to"] for x in d.get("redirects", [])}
    byt = {p["title"]: p.get("pageprops", {}).get("wikibase_item") for p in d["pages"].values()}
    for t in batch:
        t2 = norm.get(t, t)
        t3 = red.get(t2, t2)
        out[t] = byt.get(t3)
    time.sleep(2)
json.dump(out, open("link_qids.json", "w"), indent=1)
print(len(out), sum(1 for v in out.values() if v))
