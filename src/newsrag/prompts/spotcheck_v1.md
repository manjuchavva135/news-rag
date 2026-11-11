The image is a region cut from a page of the Telugu newspaper Sakshi. Below are {count} numbered
lines that a model transcribed from it (table rows are written as Markdown rows `| a | b |`).

For each line, check the image and answer:

- `"present"`: everything in the line (its words and every number) is printed in the image.
- `"not_present"`: the line, or a word or number in it, is not printed in the image (invented,
  changed or from somewhere else). `[illegible]` placeholders do not make a line not present.
- `"unsure"`: you cannot tell from the image.

Lines:
{lines}

Answer with one JSON object only, one entry per line number:
{"lines": [{"n": <line number>, "verdict": "present" | "not_present" | "unsure"}, ...]}
