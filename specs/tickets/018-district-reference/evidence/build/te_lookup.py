import json, time, urllib.parse, urllib.request, urllib.error
UA = "newsrag-geo/0.1 (research script) python-urllib"
guess = {
 "Q122949093": "కరీంనగర్", "Q135553522": "ఇనుగుర్తి", "Q135553523": "ఆలూర్", "Q135553520": "భోరజ్",
 "Q135553521": "డొంకేశ్వర్", "Q135553526": "ఎర్రవల్లి", "Q135553524": "కుకునూర్‌పల్లి",
 "Q135553525": "గుడిపల్లి", "Q135553530": "గుండుమాల్", "Q135553528": "పాల్వంచ",
 "Q135553534": "భీమారం", "Q135553535": "పోతంగల్", "Q135553532": "సాలూర", "Q135553533": "సాత్నాల",
 "Q135553539": "మల్లంపల్లి", "Q135553536": "సీరోల్", "Q135553537": "నిజాంపేట్",
 "Q135553542": "గట్టుప్పల్", "Q135553540": "కొత్తపల్లి", "Q135553541": "కొత్తపల్లిగోరి",
 "Q135553546": "దుద్యాల్", "Q135553547": "ఎండపల్లి", "Q135553544": "మహమ్మద్‌నగర్",
 "Q135553548": "కౌకుంట్ల", "Q135553549": "ఏదుల",
}
out = {}
for q, g in guess.items():
    s = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": g + " మండలం", "srlimit": 3, "format": "json"})
    for k in range(5):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request("https://te.wikipedia.org/w/api.php?" + s, headers={"User-Agent": UA})))
            break
        except urllib.error.HTTPError as e:
            time.sleep(10 * (k + 1))
    titles = [x["title"] for x in d["query"]["search"]]
    print(q, g, titles)
    out[q] = g
    time.sleep(1.5)
json.dump(out, open("te_extra.json", "w"), ensure_ascii=False, indent=1)
