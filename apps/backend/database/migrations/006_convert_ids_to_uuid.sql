-- +goose Up
-- +goose StatementBegin
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Goose runs this migration in one transaction. Keep the legacy IDs available
-- until every reference has been translated, and prevent concurrent writes.
LOCK TABLE users, organizations, user_organizations, secrets IN ACCESS EXCLUSIVE MODE;

-- created_by was unconstrained TEXT in migration 003. An unknown author needs
-- operator attention; never silently discard a secret or assign it a new owner.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM secrets s
        LEFT JOIN users u ON s.created_by = u.id::text
        WHERE u.id IS NULL
    ) THEN
        RAISE EXCEPTION 'Cannot migrate secrets: created_by must reference an existing legacy user ID'
            USING ERRCODE = '23503';
    END IF;
END $$;

CREATE TEMP TABLE migration_006_user_ids ON COMMIT DROP AS
    SELECT id AS legacy_id, uuid_generate_v4() AS uuid_id FROM users;
CREATE UNIQUE INDEX ON migration_006_user_ids (legacy_id);
CREATE TEMP TABLE migration_006_organization_ids ON COMMIT DROP AS
    SELECT id AS legacy_id, uuid_generate_v4() AS uuid_id FROM organizations;
CREATE UNIQUE INDEX ON migration_006_organization_ids (legacy_id);

-- These names come from migrations 002 and 004, not PostgreSQL's default names.
ALTER TABLE users DROP CONSTRAINT fk_users_organization;
ALTER TABLE secrets DROP CONSTRAINT fk_secrets_organization;
ALTER TABLE user_organizations DROP CONSTRAINT user_organizations_user_id_fkey;
ALTER TABLE user_organizations DROP CONSTRAINT user_organizations_organization_id_fkey;

ALTER TABLE users ADD COLUMN temp_id UUID, ADD COLUMN temp_organization_id UUID;
ALTER TABLE organizations ADD COLUMN temp_id UUID;
ALTER TABLE user_organizations
    ADD COLUMN temp_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    ADD COLUMN temp_user_id UUID,
    ADD COLUMN temp_organization_id UUID;
ALTER TABLE secrets
    ADD COLUMN temp_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    ADD COLUMN temp_created_by UUID,
    ADD COLUMN temp_organization_id UUID;

UPDATE users u SET temp_id = m.uuid_id
    FROM migration_006_user_ids m WHERE u.id = m.legacy_id;
UPDATE organizations o SET temp_id = m.uuid_id
    FROM migration_006_organization_ids m WHERE o.id = m.legacy_id;
UPDATE users u SET temp_organization_id = m.uuid_id
    FROM migration_006_organization_ids m WHERE u.organization_id = m.legacy_id;
UPDATE user_organizations uo
    SET temp_user_id = u.uuid_id, temp_organization_id = o.uuid_id
    FROM migration_006_user_ids u, migration_006_organization_ids o
    WHERE uo.user_id = u.legacy_id AND uo.organization_id = o.legacy_id;
UPDATE secrets s SET temp_created_by = m.uuid_id
    FROM migration_006_user_ids m WHERE s.created_by = m.legacy_id::text;
UPDATE secrets s SET temp_organization_id = m.uuid_id
    FROM migration_006_organization_ids m WHERE s.organization_id = m.legacy_id;

-- Replacing only ID columns preserves all business fields, encrypted payloads
-- and timestamps. PostgreSQL removes indexes/unique constraints on old columns;
-- recreate their UUID equivalents below.
ALTER TABLE users DROP COLUMN id, DROP COLUMN organization_id;
ALTER TABLE organizations DROP COLUMN id;
ALTER TABLE user_organizations DROP COLUMN id, DROP COLUMN user_id, DROP COLUMN organization_id;
ALTER TABLE secrets DROP COLUMN id, DROP COLUMN created_by, DROP COLUMN organization_id;

ALTER TABLE users RENAME COLUMN temp_id TO id;
ALTER TABLE users RENAME COLUMN temp_organization_id TO organization_id;
ALTER TABLE organizations RENAME COLUMN temp_id TO id;
ALTER TABLE user_organizations RENAME COLUMN temp_id TO id;
ALTER TABLE user_organizations RENAME COLUMN temp_user_id TO user_id;
ALTER TABLE user_organizations RENAME COLUMN temp_organization_id TO organization_id;
ALTER TABLE secrets RENAME COLUMN temp_id TO id;
ALTER TABLE secrets RENAME COLUMN temp_created_by TO created_by;
ALTER TABLE secrets RENAME COLUMN temp_organization_id TO organization_id;

ALTER TABLE users ADD PRIMARY KEY (id), ALTER COLUMN id SET DEFAULT uuid_generate_v4();
ALTER TABLE organizations ADD PRIMARY KEY (id), ALTER COLUMN id SET DEFAULT uuid_generate_v4();
ALTER TABLE user_organizations
    ADD PRIMARY KEY (id),
    ALTER COLUMN user_id SET NOT NULL,
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT user_organizations_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    ADD CONSTRAINT user_organizations_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    ADD CONSTRAINT user_organizations_user_id_organization_id_key UNIQUE (user_id, organization_id);
ALTER TABLE users ADD CONSTRAINT users_organization_id_fkey
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE secrets
    ADD PRIMARY KEY (id),
    ALTER COLUMN created_by SET NOT NULL,
    ADD CONSTRAINT secrets_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL,
    ADD CONSTRAINT secrets_app_name_org_unique UNIQUE (app, name, organization_id);

CREATE INDEX idx_users_organization_id ON users(organization_id);
CREATE INDEX idx_user_organizations_user_id ON user_organizations(user_id);
CREATE INDEX idx_user_organizations_organization_id ON user_organizations(organization_id);
CREATE INDEX idx_secrets_created_by ON secrets(created_by);
CREATE INDEX idx_secrets_organization_id ON secrets(organization_id);
-- +goose StatementEnd

-- +goose Down
-- +goose StatementBegin
DO $$
BEGIN
    RAISE EXCEPTION 'Migration 006 cannot be reversed safely; restore a pre-migration backup instead';
END $$;
-- +goose StatementEnd
