-- Pipeline ledger (ticket 003). Unqualified names: `newsrag ledger init` runs this with
-- search_path set to settings.ledger_schema.

create table if not exists pages (
    page_id           text primary key,
    pub_date          date not null,
    edition_code      text not null,
    section           text not null,
    page_num          int not null,
    page_end          int null,
    source_path       text not null,
    source_archive    text not null,
    revision          int not null default 1,
    quarantine_reason text null,
    registered_at     timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);
create index if not exists pages_pub_date_idx on pages (pub_date);

create table if not exists stage_runs (
    page_id        text not null references pages (page_id) on delete cascade,
    stage          text not null,
    version        text not null,
    input_versions jsonb not null default '{}'::jsonb,
    status         text not null check (status in ('running', 'done', 'failed')),
    attempt        int not null default 0,
    artifact_path  text null,
    detail         jsonb null,
    error          text null,
    started_at     timestamptz null,
    finished_at    timestamptz null,
    primary key (page_id, stage)
);
create index if not exists stage_runs_stage_status_idx on stage_runs (stage, status);

create table if not exists date_runs (
    pub_date       date not null,
    stage          text not null,
    version        text not null,
    input_versions jsonb not null default '{}'::jsonb,
    status         text not null check (status in ('running', 'done', 'failed')),
    attempt        int not null default 0,
    artifact_path  text null,
    detail         jsonb null,
    error          text null,
    started_at     timestamptz null,
    finished_at    timestamptz null,
    primary key (pub_date, stage)
);
create index if not exists date_runs_stage_status_idx on date_runs (stage, status);

create table if not exists quarantine (
    source_path    text primary key,
    source_archive text null,
    reason         text not null,
    detected_at    timestamptz not null default now()
);
