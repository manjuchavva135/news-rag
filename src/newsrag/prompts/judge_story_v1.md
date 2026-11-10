You check one article cut out of a page of a Telugu daily newspaper (Sakshi). Another system collected the text boxes below into this article. Sometimes it wrongly joins two or more different news stories into one article.

Read the headline boxes and the text and decide: is this ONE news story, or TWO OR MORE different stories joined together?

- One story: one event or topic. A headline with a kicker, a deck, sub-headings, a quote box, a small table or a photo caption that belong to the same story are fine.
- Two or more: the text switches to another event, place or topic, usually with its own headline and its own dateline (for example "సాక్షి, అమరావతి:" starting again).

Return one JSON object and nothing else, on one line: {"stories": 1} for one story, {"stories": 2} for two or more.

Headline boxes: {headlines}

Text:
{text}
