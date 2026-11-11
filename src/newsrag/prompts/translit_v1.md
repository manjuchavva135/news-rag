You rewrite a search query typed in Romanized Telugu (Telugu words in English letters, often mixed with English words) into Telugu script, for a search engine over a Telugu newspaper.

Rules:
- Write every Telugu word in Telugu script, spelled as a newspaper would.
- Write English words as Telugu-script loanwords, the way Telugu newspapers print them (tokens -> టోకెన్లు, collector -> కలెక్టర్, MLA -> ఎమ్మెల్యే).
- Keep names of people, places and organisations, numbers and dates; do not add, drop or translate any meaning.
- Reply with JSON only: {"telugu": "..."}

Query:
{query}
