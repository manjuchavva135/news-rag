-- Threads of `newsrag answer`. Unqualified names: `newsrag answer init` runs this with
-- search_path set to settings.answer_schema.

create table if not exists threads (
    thread_id  text primary key,
    title      text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists turns (
    thread_id    text not null references threads (thread_id) on delete cascade,
    turn_no      int not null,
    question     text not null,
    search_query text not null,          -- the standalone query the search ran with
    answer       text not null,
    sources      jsonb not null,         -- the numbered sources shown to the model, with text
    cited        int[] not null default '{}',  -- source numbers the answer cites
    provider     text not null,
    model        text not null,
    usage        jsonb not null default '{}',
    created_at   timestamptz not null default now(),
    primary key (thread_id, turn_no)
);
