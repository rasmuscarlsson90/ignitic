-- +goose Up
-- +goose StatementBegin
-- Migration 006 already converted the existing tables to UUIDs. Add the
-- remaining relationships in place instead of dropping tables and their data.
-- Legacy organizations have no recorded creator: retain NULL rather than
-- inventing ownership. New organizations supply created_by in the application.
ALTER TABLE organizations ADD COLUMN created_by UUID;
ALTER TABLE organizations ADD CONSTRAINT organizations_created_by_fkey
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE secrets ADD CONSTRAINT secrets_created_by_fkey
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE;
-- +goose StatementEnd

-- +goose Down
-- +goose StatementBegin
-- Dropping the creator column could destroy ownership added after deployment.
DO $$
BEGIN
    RAISE EXCEPTION 'Migration 007 cannot be reversed safely; restore a pre-migration backup instead';
END $$;
-- +goose StatementEnd
