"""Answers in threads (`newsrag answer`): a question is answered from the stores with the
earlier turns of its thread as context.

- `threads`: the threads and their turns in Postgres (`settings.answer_schema`).
- `context`: what of a thread goes to the model (history to a token budget, carried sources).
- `llm`: the two providers, a local vLLM profile and Claude on Bedrock, both with reasoning.
- `service`: one turn = rewrite the follow-up, search, answer, store.

Importing this package touches neither the stores, the GPU nor the network.
"""
