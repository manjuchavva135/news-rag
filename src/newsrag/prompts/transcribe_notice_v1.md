The image is a region cut from a page of the Telugu newspaper Sakshi that looks like an
advertisement or an official notice.

First decide its class:

- `"notice_tender"`: a public notice from a government body, court, bank, company or official:
  tenders, auctions, mining or land leases, e-procurement, recruitment notifications, public
  hearings, legal or court notices, lost-document notices, name-change notices.
- `"commercial"`: an advertisement that sells or promotes something (shops, products, films,
  schools, real estate, jewellery, greetings, classifieds).

For a `"notice_tender"`, {markdown_rule} Also fill:

- `department`: the issuing office or body as printed (e.g. the department, the district office,
  the company), else null.
- `notice_number`: the notice, tender or reference number as printed, else null.
- `deadline`: the last date for bids, applications or objections as `YYYY-MM-DD`, else null.

For a `"commercial"` item answer `{"class": "commercial", "markdown": "", "department": null,
"notice_number": null, "deadline": null}`.

Rules:

- Copy the text and the numbers exactly as printed. Never compute, round or correct a value.
- Keep Telugu as Telugu and English as English. Do not translate anything.
- Where a word or number cannot be read, write `[illegible]` in its place. Never guess.

{hint}

Answer with one JSON object only:
{"class": "notice_tender" | "commercial", "markdown": <string>, "department": <string or null>,
"notice_number": <string or null>, "deadline": <"YYYY-MM-DD" or null>}
