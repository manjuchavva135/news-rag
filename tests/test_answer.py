"""`newsrag answer`: thread context, carried sources, the two providers and one turn, with a
fake search, fake models and an in-memory thread store."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from newsrag import config
from newsrag.answer import context as ctx
from newsrag.answer import llm as llm_mod
from newsrag.answer import service
from newsrag.answer.threads import Turn, UnknownThread
from newsrag.search.hybrid import Hit


@pytest.fixture
def settings(monkeypatch):
    def update(**values):
        current = config.settings.model_copy(update=values)
        monkeypatch.setattr(config, "settings", current)
        return current

    update(answer_chars_per_token=1.0, answer_history_tokens=1000, answer_carry_sources=6)
    return update


class MemoryStore:
    def __init__(self):
        self.threads: dict[str, list[Turn]] = {}

    def create_thread(self, question):
        thread_id = f"t{len(self.threads) + 1}"
        self.threads[thread_id] = []
        return thread_id

    def load_turns(self, thread_id):
        if thread_id not in self.threads:
            raise UnknownThread(thread_id)
        return list(self.threads[thread_id])

    def add_turn(self, thread_id, turn):
        turn.turn_no = len(self.threads[thread_id]) + 1
        self.threads[thread_id].append(turn)
        return turn.turn_no


class FakeLLM:
    provider, model = "fake", "fake-1"

    def __init__(self, answers, rewrites=()):
        self.answers, self.rewrites = list(answers), list(rewrites)
        self.calls: list[dict] = []

    def complete(self, system, messages, max_tokens, thinking):
        self.calls.append({"system": system, "messages": list(messages), "thinking": thinking})
        if not thinking:  # the rewrite
            reply = self.rewrites.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return llm_mod.Reply(reply)
        return llm_mod.Reply(self.answers.pop(0), reasoning="thought about it")


def hit(chunk, story="s1", text="text"):
    return Hit(chunk_id=chunk, story_id=story, score=1.0, page_ids=["P1"], text=text,
               headline=f"head {chunk}", pub_date="2026-10-08")  # fmt: skip


def run(question, thread, llm, store, hits):
    seen = []

    def search(query, k):
        seen.append(query)
        return hits

    result = service.ask(
        question, thread, llm=llm, store=store, search_fn=search,
        story_meta=lambda ids: {"s1": {"page_id": "P9", "editions": ["Guntur"]}},
        covered_years=[2026], today=date(2026, 11, 12),
    )  # fmt: skip
    return result, seen


# --- history ------------------------------------------------------------------------------------


def turn(n, q, a, sources=(), cited=()):
    return Turn(turn_no=n, question=q, answer=a, sources=list(sources), cited=list(cited))


def test_history_sends_whole_turns_newest_first_until_the_budget(settings):
    turns = [turn(i, f"q{i}" + "x" * 98, f"a{i}" + "y" * 198) for i in range(1, 6)]  # 300 each
    h = ctx.history(turns, 1000)
    assert (h.kept, h.dropped, h.tokens) == (3, 2, 900)
    assert [m["role"] for m in h.messages] == ["user", "assistant"] * 3
    assert (
        h.messages[0]["content"].startswith("q3") and h.messages[-1]["content"] == turns[-1].answer
    )


def test_history_always_sends_the_newest_turn_cut_to_fit(settings):
    turns = [turn(1, "old", "old answer"), turn(2, "q" * 100, "a" * 5000)]
    h = ctx.history(turns, 1000)
    assert (h.kept, h.dropped) == (1, 1)
    assert h.messages[0]["content"] == "q" * 100 and len(h.messages[1]["content"]) == 900


def test_history_of_a_new_thread_is_empty(settings):
    assert ctx.history([], 1000).messages == []


# --- sources ------------------------------------------------------------------------------------


def test_sources_keep_their_number_in_the_thread_and_cited_ones_are_carried(settings):
    first = ctx.sources_for([hit("c1"), hit("c2"), hit("c3")], {}, [])
    assert [s.n for s in first] == [1, 2, 3]
    turns = [turn(1, "q", "a [2][3]", [s.record() for s in first], [2, 3])]
    second = ctx.sources_for([hit("c3"), hit("c9")], {}, turns)
    # c3 keeps 3, the new chunk gets 4, the cited c2 is carried; uncited c1 is not
    assert [(s.n, s.chunk_id, s.carried) for s in second] == [
        (3, "c3", False), (4, "c9", False), (2, "c2", True)
    ]  # fmt: skip


def test_carried_sources_are_limited_newest_turn_first(settings):
    one = ctx.sources_for([hit("c1"), hit("c2")], {}, [])
    turns = [turn(1, "q", "a", [s.record() for s in one], [1, 2])]
    two = ctx.sources_for([hit("c5")], {}, turns, carry=0)
    turns.append(turn(2, "q", "a", [s.record() for s in two], [3]))
    out = ctx.sources_for([hit("c7")], {}, turns, carry=2)
    assert [(s.n, s.carried) for s in out] == [(4, False), (3, True), (1, True)]


def test_cited_numbers_only_counts_shown_sources():
    assert ctx.cited_numbers("x [2][7] y [1] z [2]", [1, 2, 3]) == [2, 1]


# --- one turn -----------------------------------------------------------------------------------


def test_follow_up_gets_the_thread_and_its_sources(settings):
    store, llm = (
        MemoryStore(),
        FakeLLM(["first answer [1]", "second answer [1][3]"], ["Amaravati funds 2026"]),
    )
    first, seen = run("What about Amaravati funds?", None, llm, store, [hit("c1"), hit("c2")])
    assert (first.thread_id, first.turn_no, first.history_turns, first.cited) == ("t1", 1, 0, [1])
    assert seen == ["What about Amaravati funds?"] and len(llm.calls) == 1  # no rewrite on turn 1
    assert first.reasoning == "thought about it"

    second, seen = run("and how much of it was spent?", "t1", llm, store, [hit("c8")])
    assert seen == ["Amaravati funds 2026"] and second.search_query == "Amaravati funds 2026"
    assert (second.turn_no, second.history_turns, second.dropped_turns) == (2, 1, 0)
    messages = llm.calls[-1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "What about Amaravati funds?"  # history: no sources
    assert messages[1]["content"] == "first answer [1]"
    final = messages[2]["content"]
    assert "and how much of it was spent?" in final
    assert "[3] head c8" in final and "[1] head c1" in final and "head c2" not in final
    assert "Guntur | page P9" in final
    assert second.cited == [1, 3]
    stored = store.threads["t1"][1]
    assert stored.search_query == "Amaravati funds 2026" and stored.model == "fake-1"
    assert not hasattr(stored, "reasoning")  # the reasoning is not stored


@pytest.mark.parametrize("bad", ["", "two\nlines", "x" * 700, RuntimeError("down")])
def test_a_bad_rewrite_falls_back_to_the_question(settings, bad):
    store, llm = MemoryStore(), FakeLLM(["a", "b"], [bad])
    run("first", None, llm, store, [hit("c1")])
    result, seen = run("follow-up", "t1", llm, store, [hit("c1")])
    assert seen == ["follow-up"] and result.search_query == "follow-up"


def test_no_hits_still_answers_and_says_so_to_the_model(settings):
    store, llm = MemoryStore(), FakeLLM(["nothing found"])
    result, _ = run("anything?", None, llm, store, [])
    assert result.sources == [] and service.NO_SOURCES in llm.calls[0]["messages"][-1]["content"]


def test_unknown_thread_and_empty_question(settings):
    with pytest.raises(UnknownThread):
        run("q", "nope", FakeLLM(["a"]), MemoryStore(), [])
    with pytest.raises(ValueError):
        run("  ", None, FakeLLM(["a"]), MemoryStore(), [])


def test_system_prompt_has_the_date_and_the_years():
    text = service.system_prompt(date(2026, 11, 12), [2020, 2026])
    assert "2026-11-12" in text and "2020, 2026" in text and "{" not in text


# --- providers ----------------------------------------------------------------------------------


def test_split_reasoning():
    assert llm_mod.split_reasoning("<think>why</think>\nanswer") == ("why", "answer")
    assert llm_mod.split_reasoning("why</think>answer") == ("why", "answer")
    assert llm_mod.split_reasoning("answer") == ("", "answer")


def local_client(content, reasoning_content=None, finish="stop"):
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        msg = SimpleNamespace(content=content, reasoning_content=reasoning_content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    raw = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return SimpleNamespace(raw=raw, profile="qwen-x"), sent


def test_local_llm_turns_thinking_on_and_splits_the_reasoning(settings):
    client, sent = local_client("because</think>the answer [1]")
    reply = llm_mod.LocalLLM(client).complete("sys", [{"role": "user", "content": "q"}], 100, True)
    assert (reply.text, reply.reasoning) == ("the answer [1]", "because")
    assert reply.usage == {"input_tokens": 10, "output_tokens": 5}
    assert sent["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}
    assert sent["temperature"] == config.settings.answer_thinking_temperature
    assert sent["messages"][0] == {"role": "system", "content": "sys"}

    client, sent = local_client("answer", reasoning_content="parsed by vllm")
    reply = llm_mod.LocalLLM(client).complete("sys", [], 100, False)
    assert (reply.text, reply.reasoning) == ("answer", "parsed by vllm")
    assert sent["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["temperature"] == config.settings.answer_temperature


def test_local_llm_that_only_reasoned_is_an_error(settings):
    client, _ = local_client("<think>still thinking", finish="length")
    with pytest.raises(llm_mod.AnswerUnavailable, match="answer_max_tokens"):
        llm_mod.LocalLLM(client).complete("sys", [], 100, True)


def test_claude_payload_per_thinking_mode(settings):
    messages = [{"role": "user", "content": "q"}]
    settings(answer_claude_thinking="budget", answer_claude_thinking_budget=4000)
    p = llm_mod.claude_payload("sys", messages, 2000, True)
    assert p["thinking"] == {"type": "enabled", "budget_tokens": 4000} and p["max_tokens"] == 6000
    assert p["system"] == "sys" and "temperature" not in p
    settings(answer_claude_thinking="adaptive", answer_claude_effort="high")
    p = llm_mod.claude_payload("sys", messages, 2000, True)
    assert p["thinking"]["type"] == "adaptive" and p["output_config"] == {"effort": "high"}
    assert p["max_tokens"] == 2000
    assert "thinking" not in llm_mod.claude_payload("sys", messages, 2000, False)
    settings(answer_claude_thinking="loud")
    with pytest.raises(llm_mod.AnswerUnavailable):
        llm_mod.claude_payload("sys", messages, 2000, True)


def test_parse_claude():
    reply = llm_mod.parse_claude({
        "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": " ok [1] "}],
        "stop_reason": "end_turn", "usage": {"input_tokens": 7, "output_tokens": 3},
    })  # fmt: skip
    assert (reply.text, reply.reasoning, reply.usage["input_tokens"]) == ("ok [1]", "hmm", 7)
    with pytest.raises(llm_mod.AnswerUnavailable, match="declined"):
        llm_mod.parse_claude({"content": [], "stop_reason": "refusal"})
    with pytest.raises(llm_mod.AnswerUnavailable, match="max_tokens"):
        llm_mod.parse_claude({"content": [], "stop_reason": "max_tokens"})


def test_claude_needs_a_key(settings):
    settings(bedrock_api_key=None)
    with pytest.raises(llm_mod.AnswerUnavailable, match="AWS_BEARER_TOKEN_BEDROCK"):
        llm_mod.ClaudeLLM()


def test_embed_device(settings):
    settings(answer_embed_device="auto")
    assert (service.embed_device("local"), service.embed_device("claude")) == ("cpu", "cuda")
    settings(answer_embed_device="gpu")
    with pytest.raises(ValueError):
        service.embed_device("local")


def test_cli_lists_the_answer_commands():
    from newsrag.cli import app

    out = CliRunner().invoke(app, ["answer", "--help"]).output
    assert all(name in out for name in ("init", "ask", "chat", "threads", "show"))


# --- the Postgres store -------------------------------------------------------------------------


@pytest.mark.stores
def test_pg_store_round_trip(settings):
    import secrets

    from psycopg import sql

    from newsrag.answer import threads
    from newsrag.stores import pg

    name = f"test_{secrets.token_hex(6)}"
    settings(answer_schema=name)
    try:
        assert threads.migrate() == ["001_init.sql"] and threads.migrate() == []
        store = threads.PgStore()
        thread_id = store.create_thread("  first   question  ")
        src = ctx.sources_for([hit("c1")], {}, [])[0].record()
        for q in ("first question", "second"):
            n = store.add_turn(thread_id, Turn(0, q, f"answer to {q} [1]", q, [src], [1], "fake",
                                               "fake-1", {"input_tokens": 3}))  # fmt: skip
        assert n == 2
        turns = store.load_turns(thread_id)
        assert [(t.turn_no, t.question) for t in turns] == [(1, "first question"), (2, "second")]
        assert turns[0].sources[0]["chunk_id"] == "c1" and turns[0].cited == [1]
        assert turns[0].usage == {"input_tokens": 3}
        (row,) = store.list_threads()
        assert (row["title"], row["turns"]) == ("first question", 2)
        with pytest.raises(UnknownThread):
            store.load_turns("nope")
    finally:
        with pg().connection() as c:
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def test_answer_language_follows_the_script_of_the_question():
    assert service.answer_language("తిరుమల టోకెన్లు ఎప్పుడు?") == "Telugu"
    assert service.answer_language("When are the tokens issued?").startswith("English")
    assert "in English" in service.question_message("When?", [])
