-- Persistent entity ids of the name alias table (ticket 022). One row per name:
-- name_key = '<type>|<normalised name>' (newsrag.aliases.key). A name keeps its row for good;
-- when entities merge, the names of the losing entity are pointed to the winner's id.
-- created_at is the time the name first got an id (it decides which id of a merged group wins).
create table if not exists entity_ids (
    name_key   text primary key,
    entity_id  text not null,
    created_at timestamptz not null default now()
);
create index if not exists entity_ids_entity_id_idx on entity_ids (entity_id);
