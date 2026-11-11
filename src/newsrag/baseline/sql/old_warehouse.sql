-- The old pipeline's warehouse tables, copied from /home/admin1/RAG/docker/postgres/init/01_warehouse.sql
-- (ticket 027 FR-2): issues, pages, articles, chunks only. Unqualified names: the loader runs this
-- with search_path set to settings.baseline_schema (`old`).

CREATE TABLE IF NOT EXISTS issues (
    issue_id   TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pages (
    page_id    TEXT PRIMARY KEY,
    issue_id   TEXT NOT NULL REFERENCES issues(issue_id),
    page_num   INTEGER,
    source_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS articles (
    article_id TEXT PRIMARY KEY,
    story_id   TEXT NOT NULL,
    page_id    TEXT NOT NULL REFERENCES pages(page_id),
    issue_id   TEXT NOT NULL REFERENCES issues(issue_id),
    seq        INTEGER,
    headline   TEXT,
    pub_date   DATE,
    state      TEXT,
    edition    TEXT,
    category   TEXT CHECK (category IN
        ('sports', 'politics', 'health', 'cinema', 'family', 'education', 'other')),
    synopsis   TEXT,
    topics     JSONB,
    entities   JSONB,
    lang       TEXT,
    body       TEXT,
    char_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_articles_page  ON articles(page_id);
CREATE INDEX IF NOT EXISTS idx_articles_story ON articles(story_id);
CREATE INDEX IF NOT EXISTS idx_articles_state_date ON articles(state, pub_date);
CREATE INDEX IF NOT EXISTS idx_articles_category   ON articles(category);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES articles(article_id),
    story_id   TEXT NOT NULL,
    page_id    TEXT NOT NULL REFERENCES pages(page_id),
    issue_id   TEXT NOT NULL REFERENCES issues(issue_id),
    seq        INTEGER NOT NULL,
    lang       TEXT,
    text       TEXT NOT NULL,
    token_est  INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_article ON chunks(article_id);
CREATE INDEX IF NOT EXISTS idx_chunks_page    ON chunks(page_id);
