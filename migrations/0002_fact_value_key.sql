-- Retrieval has to be able to match on what a fact SAYS, not only what it is
-- about. "Endokrinolog kim?" names no subject and no attribute -- "endokrinolog"
-- is a *value* of the lavozim attribute. Without this the reference question in
-- the brief ("which doctors work there and their specialties") is unanswerable.
--
-- Nullable, and written by the app like every other _key column, so there stays
-- exactly one normalization implementation.
alter table fact add column value_key text;

-- Partial index: only short values are plausible search terms. A value like
-- "Dushanba-Shanba, 09:00-18:00" will never appear inside a question, so there
-- is no reason to carry it in the index.
create index fact_value_key_idx on fact (value_key)
    where value_key is not null and length(value_key) <= 40;
