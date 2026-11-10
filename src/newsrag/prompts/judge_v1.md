You are checking how well a page of a Telugu daily newspaper (Sakshi) was cut into articles. Another system grouped the text boxes of the page into articles. The page image shows the result: each article is painted in its own colour, and its number and kind are written at its top-left corner. The articles are listed below the instructions with their headline and the start of their text.

Your job is to judge every listed article, then count the stories the system missed. Look at the picture, not only at the text.

## What to return

Return one JSON object and nothing else (no explanation, no code fence). Write it compactly on a single line, exactly in this shape:

{"articles":[{"n":1,"verdict":"correct"},{"n":2,"verdict":"merged"}],"missed_regions":0}

- `articles` has exactly one entry for every article in the list, with the article's number as `n`. Use each number once.
- `verdict` is one of:
  - `correct`: the coloured area is exactly one story, with its headline and all of its text, and nothing from another story.
  - `merged`: the area holds two or more different stories (for example the items of an "in brief" column, or two neighbouring stories).
  - `split`: the area is only a part of a story; the rest of the story (its other columns, its headline or its body) sits in another article or is not painted.
  - `wrong_blocks`: it is one story but it also holds text that belongs elsewhere, or it lacks parts that are not part of any other article (for example a photo caption of another story, a stray column).
  - `not_an_article`: the area is not a story at all (a table that belongs to a story, a stray line, a page number).
- `missed_regions` is the number of stories you can see on the page that are not painted by any article (a story whose text is not coloured at all). Do not count advertisements and the newspaper's name or page header. Use 0 when nothing is missed.

## Rules

- A story with a headline, a byline, text in several columns and its photo with caption is one `correct` article when all of that is painted in one colour.
- Two stories that are painted in the same colour, or one article that covers two headlines, are `merged`.
- The same story painted in two colours is `split` for both articles.
- Mastheads and advertisements are not listed and not painted: do not judge them and do not count them as missed.

## Articles on this page ({count})

{articles}
