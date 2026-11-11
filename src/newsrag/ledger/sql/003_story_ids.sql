-- Stable story ids of the cross-edition dedup (ticket 017). One row per dedup unit (a stitched
-- article id or a standalone item id); the units of one story share a story_id. `first_seen` is
-- the day the unit first got its current story id.
create table if not exists story_ids (
    unit_id    text primary key,
    story_id   text not null,
    first_seen date not null
);
create index if not exists story_ids_story_id_idx on story_ids (story_id);
