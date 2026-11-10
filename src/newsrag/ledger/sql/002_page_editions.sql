-- Edition names, districts and states of each page (ticket 011). Replaced as a whole by
-- `newsrag editions apply`.
create table if not exists page_editions (
    page_id            text primary key references pages (page_id) on delete cascade,
    edition_names      text[] not null default '{}',
    districts_current  text[] not null default '{}',
    districts_at_print text[] not null default '{}',
    states             text[] not null default '{}',
    source             text not null,
    flags              text[] not null default '{}'
);
