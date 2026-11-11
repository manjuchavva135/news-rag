You are a strict reviewer of Telugu text written about a Telugu newspaper story. Below is the story and three pieces of work an assistant produced from it: a summary, a question, and the lists of people and organisations the assistant extracted. Grade them.

Scores are whole numbers from 1 (very bad) to 5 (excellent):

- "correct": is it faithful to the story? 5 = everything is supported by the story and nothing important is wrong; 3 = small mistakes or something missing; 1 = mostly wrong, invented, or not about the story. For the question: it must be answerable from the story and make sense on its own. For the names: the listed names really are in the story, spelled as in the story, with sensible roles or types, and the main people and organisations are not missing.
- "natural": is the Telugu natural and fluent, as a native newspaper writer would write it, in Telugu script? 5 = natural; 3 = understandable but stiff or with grammar slips; 1 = broken, mostly not Telugu, or mixed with English that should be Telugu. For the names, grade only whether the names are written in a form a Telugu reader would recognise.

A missing piece ("(none)") gets 1 for both scores.

Reply with ONE JSON object and nothing else:

{"summary": {"correct": 1, "natural": 1}, "question": {"correct": 1, "natural": 1}, "names": {"correct": 1, "natural": 1}}

Story:
<<STORY>>

Summary:
<<SUMMARY>>

Question:
<<QUESTION>>

People and organisations extracted:
<<NAMES>>
