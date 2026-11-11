You check the metadata that another system extracted from one news story of a Telugu daily newspaper (Sakshi). The story is below, then the extracted fields.

Check two things:

1. category_ok: is the category right? The category rules are:

<<RULES>>

2. names_ok: is every listed person and organisation a real person or organisation named in the story text? An empty list is fine. A name that is not in the story, a place, or something that is not a person or organisation makes it false.

Return one JSON object and nothing else, on one line: {"category_ok": true or false, "names_ok": true or false}

Story (headline, then text):
<<STORY>>

Extracted category: <<CATEGORY>>
Extracted people: <<PEOPLE>>
Extracted organisations: <<ORGANISATIONS>>
