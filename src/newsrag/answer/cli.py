"""`newsrag answer`: ask the archive in threads.

newsrag answer init                       create the thread tables
newsrag answer ask "<question>"           a new thread; prints its id
newsrag answer ask "<follow-up>" -t ID    the next turn of thread ID
newsrag answer chat [-t ID]               ask turn after turn (the model stays loaded)
newsrag answer threads | show ID
"""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(help="Answers from the stores, in threads.", no_args_is_help=True)

Provider = Annotated[
    str | None, typer.Option("--provider", "-p", help="local | claude (default answer_provider)")
]
Thread = Annotated[str | None, typer.Option("--thread", "-t", help="Continue this thread.")]
Reasoning = Annotated[bool, typer.Option("--reasoning", help="Print the model's reasoning.")]


def _print(result, reasoning: bool) -> None:
    if result.search_query != result.question:
        typer.echo(f"search: {result.search_query}")
    note = f", {result.dropped_turns} older left out" if result.dropped_turns else ""
    typer.echo(f"context: {result.history_turns} earlier turn(s) sent{note}")
    if reasoning:
        typer.echo(f"\n--- reasoning ---\n{result.reasoning or '(none returned)'}\n--- answer ---")
    typer.echo(f"\n{result.answer}\n")
    for src in result.sources:
        if src.n in result.cited:
            typer.echo(f"  {src.header()}")
    typer.echo(f"thread {result.thread_id} turn {result.turn_no}")


def _fail(error: Exception) -> typer.Exit:
    typer.echo(f"{type(error).__name__}: {error}", err=True)
    return typer.Exit(1)


@app.command()
def init() -> None:
    """Create the thread tables (schema `answer_schema`)."""
    from newsrag.answer import threads

    applied = threads.migrate()
    typer.echo(f"schema {threads.schema()}: {', '.join(applied) or 'up to date'}")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="The question.")],
    thread: Thread = None,
    provider: Provider = None,
    reasoning: Reasoning = False,
) -> None:
    """Answer one question: a new thread, or the next turn of --thread."""
    from newsrag.answer import service
    from newsrag.answer.llm import AnswerUnavailable
    from newsrag.answer.threads import UnknownThread
    from newsrag.search import StoreNotConfigured

    try:
        with service.session(provider) as llm:
            result = service.ask(question, thread, llm=llm)
    except UnknownThread:
        typer.echo(f"no thread {thread}", err=True)
        raise typer.Exit(1) from None
    except (AnswerUnavailable, StoreNotConfigured, ValueError) as e:
        raise _fail(e) from None
    _print(result, reasoning)


@app.command()
def chat(thread: Thread = None, provider: Provider = None, reasoning: Reasoning = False) -> None:
    """Ask turn after turn in one thread; an empty line or Ctrl-D ends it."""
    from newsrag.answer import service
    from newsrag.answer.llm import AnswerUnavailable
    from newsrag.answer.threads import PgStore, UnknownThread
    from newsrag.search import StoreNotConfigured

    try:
        if thread:
            PgStore().load_turns(thread)
        with service.session(provider) as llm:
            while True:
                try:
                    question = input("> ").strip()
                except EOFError:
                    break
                if not question:
                    break
                try:
                    result = service.ask(question, thread, llm=llm)
                except AnswerUnavailable as e:
                    typer.echo(f"no answer: {e}", err=True)
                    continue
                thread = result.thread_id
                _print(result, reasoning)
    except UnknownThread:
        typer.echo(f"no thread {thread}", err=True)
        raise typer.Exit(1) from None
    except (AnswerUnavailable, StoreNotConfigured, ValueError) as e:
        raise _fail(e) from None


@app.command()
def threads(limit: Annotated[int, typer.Option(help="Threads to list.")] = 20) -> None:
    """The latest threads: id, turns, last use, title."""
    from newsrag.answer.threads import PgStore

    for row in PgStore().list_threads(limit):
        typer.echo(
            f"{row['thread_id']}  {row['turns']:>3}  {row['updated_at']:%Y-%m-%d %H:%M}  "
            f"{row['title']}"
        )


@app.command()
def show(thread: Annotated[str, typer.Argument(help="Thread id.")]) -> None:
    """Print a thread: every question, its search query and its answer."""
    from newsrag.answer.threads import PgStore, UnknownThread

    try:
        turns = PgStore().load_turns(thread)
    except UnknownThread:
        typer.echo(f"no thread {thread}", err=True)
        raise typer.Exit(1) from None
    for turn in turns:
        typer.echo(f"[{turn.turn_no}] Q: {turn.question}")
        if turn.search_query != turn.question:
            typer.echo(f"    search: {turn.search_query}")
        typer.echo(f"    A ({turn.model}): {turn.answer}\n")
