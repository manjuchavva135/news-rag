The image is a region cut from a page of the Telugu newspaper Sakshi. It may be a table, an
infographic (a chart, a map or a box of facts with numbers), or a photo or drawing.

Decide what it is and transcribe it:

- `"table"`: a grid of rows and columns. Write it as one Markdown table: the header row first,
  then the `|---|` separator row, then one Markdown row per printed row. Keep every row and every
  column in the printed order. If no header row is printed, use the first printed row as the
  header row; never write an empty header row. Put the table's printed title (if any) in `title`,
  not in the table.
- `"infographic"`: a chart, map or fact box. Write its title as the first line, then one line
  per value, `- <label>: <value>` (for a bar or pie chart: each bar or slice; for a fact box: each
  fact). Put the same title in `title`. Labels are printed words only: never name a colour, a
  shape or a position ("Blue", "left bar") as a label. A value with no printed label of its own
  takes the nearest printed heading (e.g. the year above it) as its label, or is written
  `- <value>`. A box of plain text with no values (a pointer such as "details on page 6", a
  slogan) is copied line by line as printed, without `label: value` reshaping.
- `"photo_no_text"`: a photo, a drawing or a cartoon with no table or figures to read. Use an
  empty `markdown` and `title: null`.

Rules:

- Copy the text and the numbers exactly as printed: same digits, same units, same signs, same
  commas and decimal points. Never compute, round, total or correct a value.
- Keep Telugu as Telugu and English as English. Do not translate anything.
- Where a word or number cannot be read, write `[illegible]` in its place. Never guess.
- Write only printed text: no dividers (`***`, `---`), no image tags or picture descriptions
  (`![...]`), no rows or lines without any printed word or number.
- Leave out nothing that is printed inside the region, and add nothing that is not printed.

{hint}

Answer with one JSON object only:
{"type": "table" | "infographic" | "photo_no_text", "title": <string or null>, "markdown": <string>}
