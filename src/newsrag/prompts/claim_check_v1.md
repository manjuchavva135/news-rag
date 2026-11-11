## cited

You check one claim from an answer against one source from a Telugu newspaper.

Label it "supported" only if the source states the claim's facts (names, places, dates, numbers, amounts, events). Paraphrase and translation are fine. A claim that adds a fact the source does not state, or contradicts the source, is "not_supported". Ignore the citation marks like [3] in the claim.

Reply with JSON only: {"label": "supported"} or {"label": "not_supported"}.

Claim:
{claim}

Source:
{source}

## uncited

You read one sentence (or one bullet item or table row) from an answer about a newspaper archive. It cites no source.

Label it "factual" if it states a fact about the world: a name, place, date, number, amount, event, decision, or what someone did or said.
Label it "not_factual" if it is a meta sentence: an introduction or a heading, a statement that the sources or the archive do not cover something, which years the archive covers, a note about missing information, or a closing remark with no facts.

Reply with JSON only: {"label": "factual"} or {"label": "not_factual"}.

Sentence:
{unit}
