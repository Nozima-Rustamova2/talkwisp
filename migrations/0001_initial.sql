-- The four tables. A fifth needs approval.
--
-- Two columns per name throughout: the display form as the owner wrote it,
-- and a *_key form produced by app.normalize.normalize(). Matching happens on
-- the key; display happens on the other. Normalization lives in Python so
-- there is exactly one implementation -- never add a SQL version.

create extension if not exists vector;

-- Where a piece of knowledge came from: a pasted note or an uploaded file.
create table source (
    id           uuid primary key default uuidv7(),
    kind         text not null check (kind in ('paste', 'file')),
    label        text,
    filename     text,
    media_type   text,
    bytes        bytea,
    content      text,
    status       text not null default 'pending'
                   check (status in ('pending', 'extracted', 'failed')),
    error        text,
    created_at   timestamptz not null default now(),
    extracted_at timestamptz
);

-- Subject + attribute + value. The core table.
create table fact (
    id            uuid primary key default uuidv7(),
    subject       text not null,
    subject_key   text not null,
    attribute     text not null,
    attribute_key text not null,
    value         text not null,
    -- Null for facts the owner typed: they are not 80% sure of their own hours.
    confidence    real,
    -- Null means the owner typed it. Not null means a file produced it. This is
    -- the typed-vs-extracted distinction, and it survives review, where a plain
    -- flag would forget. restrict: a source cannot be deleted out from under
    -- facts that cite it.
    source_id     uuid references source(id) on delete restrict,
    confirmed     boolean not null default false,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- Deliberately NO unique constraint on (subject_key, attribute_key). A subject
-- legitimately has several values for one attribute, and conflicts are a
-- judgement shown to the owner, not a constraint violation.

-- Name variants. Without these, lookup misses silently.
create table alias (
    id          uuid primary key default uuidv7(),
    -- A string, not a foreign key: there is no subject table, so a subject IS
    -- its normalized string. The price is that renaming a subject updates rows
    -- in both fact and alias.
    subject_key text not null,
    alias       text not null,
    alias_key   text not null,
    source_id   uuid references source(id) on delete restrict,
    confirmed   boolean not null default false,
    created_at  timestamptz not null default now(),
    -- Note what this permits: one alias_key mapping to several subjects.
    -- "Rasulova" may be two doctors, and the bot must be able to ask which
    -- rather than guess. A unique constraint on alias_key alone would make
    -- that unrepresentable.
    unique (alias_key, subject_key)
);

-- Prose, kept whole. The agent quotes it; it is not split into facts.
create table chunk (
    id              uuid primary key default uuidv7(),
    -- not null: a chunk with no document is meaningless. cascade, unlike fact:
    -- chunks are derived data, facts may have been confirmed by a human.
    source_id       uuid not null references source(id) on delete cascade,
    ordinal         int not null,
    content         text not null,
    -- Nullable: chunks are created in one step and embedded in another, and a
    -- failed embedding must not destroy the text.
    embedding       vector(1024),
    -- Which model produced that vector. Two models' embeddings in one column
    -- are silently meaningless -- distances between them are noise and nothing
    -- errors. This column is how re-embedding finds its work.
    embedding_model text,
    created_at      timestamptz not null default now(),
    unique (source_id, ordinal)
);

-- Step 20 looks up facts by subject, and step 19 groups by subject+attribute.
-- One composite index serves both: a b-tree on (a, b) already answers
-- queries on a alone, so a separate subject_key index would be dead weight.
create index fact_subject_attribute_idx on fact (subject_key, attribute_key);

-- Step 20's alias lookup.
create index alias_key_idx on alias (alias_key);

-- Step 17's review queue: only unconfirmed rows, oldest first. Partial, so the
-- index holds only the handful of rows awaiting review, not the whole table.
create index fact_unconfirmed_idx on fact (created_at) where not confirmed;

-- No vector index yet. ivfflat has to be built against real rows to pick its
-- list count, and hnsw's parameters depend on the embedding model. Step 16.
