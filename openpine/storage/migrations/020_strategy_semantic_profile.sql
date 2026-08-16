-- 020_strategy_semantic_profile.sql
-- Persist the admitted live/paper semantic profile so restart does not
-- silently drop it. NULL means not admitted yet.

ALTER TABLE strategy_instances ADD COLUMN semantic_profile TEXT;
