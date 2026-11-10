You are reading one page of a Telugu daily newspaper (Sakshi). The page image shows every text, headline, picture and table region as a numbered box. The same numbers are listed below the instructions, one line per block with its label, its position and the start of its text.

Your job is to group the blocks into articles and to say what each block is.

Page size: {width} x {height} pixels (300 DPI). The coordinates in the list are in these pixels: (x1,y1) is the top-left corner of a block and (x2,y2) the bottom-right corner. The image you see is the same page scaled down.

## What to return

Return one JSON object and nothing else (no explanation, no code fence). Write it compactly on a single line, with no spaces and no line breaks, exactly in this shape:

{"articles":[{"kind":"news","blocks":[{"n":12,"role":"headline"},{"n":13,"role":"body"}]},{"kind":"brief_item","blocks":[{"n":20,"role":"headline"},{"n":21,"role":"body"}]}],"unassigned":[3,7]}

- `n` is a block number from the list. Each block number must appear exactly once in the whole answer, either in one article or in `unassigned`.
- List the blocks of an article in the order a reader reads them: headline first, then the text in column order (down the first column, then the next column to the right), then pictures, captions and other parts.
- `unassigned` holds blocks that belong to no article and need no role (stray marks). Use it rarely: mastheads, ads and notices are articles of their own kind, not unassigned.

### kind (one per article)

- `news`: a news story with a headline and text.
- `editorial`: an opinion piece, column, letter, editorial or analysis.
- `brief_item`: one story of an "in brief" column or a list of short items. Every item of such a column is its own article. Never merge the stories of a briefs column into one article.
- `notice_tender`: a government notice, tender, public notice, legal or court notice.
- `table_only`: a table or chart with no story around it (results, prices, rankings, schedules).
- `ad_commercial`: a product ad, classified, matrimonial, obituary ad or any paid commercial space.
- `masthead`: the newspaper name, folio line, date line, page header or footer, section banner, page number. Put all of these in one `masthead` article per area of the page.
- `other`: anything that fits none of the above (puzzles, horoscope, weather box, cartoons).

### role (one per block)

The role names below are the only allowed values, for blocks of every kind. `masthead`, `news` and the other kinds are never roles. In a `masthead`, `ad_commercial`, `notice_tender`, `table_only` or `other` article use `headline` for a large title line, `body` for any other text, `photo` for a logo or picture, `table` for a table.

- `headline`: the main headline. A headline set in several lines or boxes: every box is a `headline` block.
- `kicker`: the short line above a headline.
- `subheadline`: the line or lines below the headline that summarise the story.
- `byline`: the writer's name or the news agency line.
- `dateline`: the place and date line that opens the text (for example the town name).
- `body`: story text.
- `caption`: the text under or beside a photo.
- `photo`: a photograph or illustration.
- `table`: a table.
- `infographic`: a chart, map or diagram.
- `pull_quote`: a quote repeated in large type in the middle of a story.
- `continuation_marker`: a line such as "continued on page 7" or "from page 1".

## Rules

- A pull-quote or a kicker belongs to the story it sits in. It never starts an article by itself.
- A photo and its caption belong to the story they illustrate.
- Two stories next to each other are two articles, even when one box touches the other. Blocks of one article form one connected area of the page.
- A `news` or `editorial` article always has a headline. A text box with no headline of its own belongs to the story of the nearest headline above it or beside it (a headline is often wider than one column, and the story's text runs in several columns under it). Do not make a separate article out of a column of text.
- A headline can be set in several boxes: lines one under the other, or boxes standing side by side at the same height that read as one sentence. All of them are `headline` blocks of the same article. A headline that ends in `..` or `...` goes on in the next headline box to its right. When the text under two neighbouring headline boxes runs on from one column to the next without a new headline between them, those boxes are one headline of one story, not two stories.
- The same words can appear in two overlapping boxes (the layout reader sometimes reads a headline twice). Do not leave such a box in `unassigned`: give it to the same article as the box it overlaps, with the same role.
- When unsure whether a block belongs to a story, look at the picture: boxes that sit together under one headline belong together.

## Blocks on this page ({count})

{blocks}
