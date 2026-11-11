-- Corpus-level steps of `newsrag run` (ticket 029): `ingest register`, `editions build` +
-- `editions apply`, `aliases build`. A step runs only when the fingerprint of its input differs
-- from the one stored here; the fingerprint is stored only after the step succeeded.
create table if not exists corpus_steps (
    step        text primary key,
    fingerprint text not null,
    finished_at timestamptz not null default now()
);
