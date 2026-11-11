# Embedding model bake-off (ticket 026)

questions: 200
corpus: 432 chunks of generation bge-m3__v1

| candidate | recall@10 | mrr | recall@10 te | recall@10 en | embed seconds |
|---|---|---|---|---|---|
| bge-m3 | 0.9950 | 0.8236 | 0.9929 | 1.0000 | 0.8 |
| qwen3-embedding-8b | 0.9850 | 0.8416 | 0.9929 | 0.9667 | 59.5 |
| multilingual-e5-large-instruct | 0.9850 | 0.8446 | 0.9929 | 0.9667 | 0.8 |

winner: multilingual-e5-large-instruct
