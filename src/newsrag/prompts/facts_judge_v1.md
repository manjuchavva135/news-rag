You check one fact that another system extracted from a news story of a Telugu daily newspaper (Sakshi). The story is below, then the extracted fact.

A fact is correct only when ALL of these are right:

1. value_text is the number as printed in the story (with its currency sign and scale word, if printed), and `value` is that number without the scale.
2. unit and scale are right. Units: INR (rupees), percent, count (people or things), acres, tonnes, km, other. Scales: none, thousand, lakh, crore, million, billion. A scale may come from the number itself or from a table's title or header (for example "(రూ. కోట్లలో)" means crore). Telugu: వేలు = thousand, లక్ష = lakh, కోటి / కోట్లు = crore. For example "₹2,500 కోట్లు" is value 2500, unit INR, scale crore. A compound "లక్ష కోట్లు" (lakh crore) is written in crore: "రూ.7.5 లక్షల కోట్ల" is value 750000, scale crore.
3. refers_to says correctly what the number measures in the story.
4. period, when given, is the period the number is about in the story (when it is "(none)", nothing is claimed).
5. place, when given, is the place the number is about (when it is "(none)", nothing is claimed).

The number must really be an amount in the story: a year, a date, a time, a phone, ID, notice or case number, a page number, a sports score, a rank or an age is NOT a valid fact.

Return one JSON object and nothing else, on one line: {"correct": true or false}

Story (headline, then text):
<<STORY>>

Extracted fact:
- value_text: <<VALUE_TEXT>>
- value: <<VALUE>>
- scale: <<SCALE>>
- unit: <<UNIT>>
- refers_to: <<REFERS_TO>>
- period: <<PERIOD>>
- place: <<PLACE>>
