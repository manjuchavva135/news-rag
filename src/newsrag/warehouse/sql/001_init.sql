-- Structured warehouse (ticket 024). Unqualified names: `newsrag warehouse init` runs this with
-- search_path set to settings.warehouse_schema.

create table if not exists pages (
    page_id            text primary key,
    pub_date           date not null,
    edition_code       text not null,
    section            text not null,
    page_num           int not null,
    page_end           int null,
    edition_names      text[] not null default '{}',
    districts_current  text[] not null default '{}',
    states             text[] not null default '{}'
);
create index if not exists pages_pub_date_idx on pages (pub_date);

create table if not exists stories (
    story_id               text primary key,
    pub_date               date not null,
    event_date             date null,
    kind                   text not null,
    category               text null,
    headline               text not null,
    text                   text not null,
    canonical_page_id      text null,
    n_printings            int not null,
    department             text null,
    notice_number          text null,
    deadline               text null,
    low_confidence         boolean not null default false,
    low_confidence_reasons text[] not null default '{}'
);
create index if not exists stories_pub_date_idx on stories (pub_date);

create table if not exists story_printings (
    story_id     text not null,
    unit_id      text not null,
    page_id      text not null,
    edition_code text not null,
    section      text null,
    page_num     int null,
    parts        jsonb not null default '[]'::jsonb,
    primary key (story_id, unit_id)
);

-- `pub_date` is not in the ticket's column list: it lets a re-index of one date replace the
-- chunks of that date in one generation even when their story has gone.
create table if not exists chunks (
    generation text not null,
    chunk_id   text not null,
    story_id   text not null,
    pub_date   date not null,
    seq        int not null,
    kind       text not null,
    headline   text not null,
    prefix     text not null,
    text       text not null,
    n_tokens   int not null,
    primary key (generation, chunk_id)
);
create index if not exists chunks_date_idx on chunks (generation, pub_date);
create index if not exists chunks_story_idx on chunks (story_id);

create table if not exists entities (
    entity_id    text primary key,
    type         text not null,
    org_type     text null,
    canonical    text not null,
    canonical_te text null,
    canonical_en text null,
    aliases      jsonb not null default '[]'::jsonb
);

create table if not exists story_entities (
    story_id      text not null,
    entity_id     text not null,
    surface_forms jsonb not null default '[]'::jsonb,
    primary key (story_id, entity_id)
);
create index if not exists story_entities_entity_idx on story_entities (entity_id);

create table if not exists story_places (
    story_id           text not null,
    seq                int not null,
    name               text not null,
    llm_type           text null,
    kind               text null,
    state              text null,
    districts_current  text[] not null default '{}',
    districts_at_print text[] not null default '{}',
    primary key (story_id, seq)
);

create table if not exists facts (
    fact_id            text primary key,
    story_id           text not null,
    value_text         text null,
    value              numeric null,
    scale              text null,
    normalized_value   numeric null,
    unit               text null,
    refers_to          text null,
    period_text        text null,
    period_start       date null,
    period_end         date null,
    place              text null,
    districts_current  text[] not null default '{}',
    districts_at_print text[] not null default '{}',
    state              text null
);
create index if not exists facts_story_idx on facts (story_id);

create table if not exists active_generation (
    singleton  boolean primary key default true check (singleton),
    generation text not null
);
