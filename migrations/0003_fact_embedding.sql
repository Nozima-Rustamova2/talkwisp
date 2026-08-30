-- Facts get vectors too, not just prose.
--
-- Exact matching cannot bridge a Russian question to an Uzbek-labelled
-- attribute: "Во сколько вы работаете?" and "ish vaqti" share no characters.
-- Vector search over facts closes that, and "what time do you open" is the most
-- common question a clinic gets.
--
-- The embedded text is "subject / attribute / value", not the bare value -- the
-- value alone ("Dushanba-Shanba, 09:00-18:00") carries no sign of what it is.
--
-- This is a fallback, not a replacement: alias and exact matching still run
-- first. See the Retrieval section of docs/design-decisions.md.
alter table fact add column embedding vector(1024);
alter table fact add column embedding_model text;
