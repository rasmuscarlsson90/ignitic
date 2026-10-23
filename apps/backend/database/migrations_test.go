//go:build integration

package database_test

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"errors"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/lib/pq"
	"github.com/pressly/goose/v3"
)

// Every test uses a newly created database. The configured database is only used
// as a maintenance connection; migrations never run against it.
func migrationDatabase(t *testing.T) (*sql.DB, *goose.Provider) {
	t.Helper()
	rawURL := os.Getenv("MIGRATION_TEST_DATABASE_URL")
	maintenanceURL, err := url.Parse(rawURL)
	if err != nil || maintenanceURL.Host == "" ||
		(maintenanceURL.Scheme != "postgres" && maintenanceURL.Scheme != "postgresql") {
		t.Fatal("MIGRATION_TEST_DATABASE_URL must be a PostgreSQL maintenance URL with CREATE DATABASE permission")
	}
	maintenanceURL.Path = "/postgres"
	maintenanceURL.RawPath = ""
	query := maintenanceURL.Query()
	query.Set("timezone", "UTC")
	maintenanceURL.RawQuery = query.Encode()
	admin, err := sql.Open("postgres", maintenanceURL.String())
	if err != nil {
		t.Fatalf("open maintenance connection: %v", err)
	}
	t.Cleanup(func() { _ = admin.Close() })

	var suffix [8]byte
	if _, err := rand.Read(suffix[:]); err != nil {
		t.Fatalf("generate isolated database name: %v", err)
	}
	databaseName := "ignitic_migrations_" + hex.EncodeToString(suffix[:])
	if _, err := admin.ExecContext(t.Context(), "CREATE DATABASE "+pq.QuoteIdentifier(databaseName)); err != nil {
		t.Fatalf("create isolated migration database: %v", err)
	}
	var db *sql.DB
	t.Cleanup(func() {
		if db != nil {
			_ = db.Close()
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		// Only this test's successfully created database can be removed here.
		if _, err := admin.ExecContext(ctx, "DROP DATABASE "+pq.QuoteIdentifier(databaseName)+" WITH (FORCE)"); err != nil {
			t.Errorf("drop isolated migration database %s: %v", databaseName, err)
		}
	})
	testURL := *maintenanceURL
	testURL.Path = "/" + databaseName
	db, err = sql.Open("postgres", testURL.String())
	if err != nil {
		t.Fatalf("open isolated migration database: %v", err)
	}
	provider, err := goose.NewProvider(goose.DialectPostgres, db, os.DirFS("migrations"))
	if err != nil {
		t.Fatalf("construct migration provider: %v", err)
	}
	return db, provider
}

func migrateTo(t *testing.T, provider *goose.Provider, version int64) {
	t.Helper()
	if _, err := provider.UpTo(t.Context(), version); err != nil {
		t.Fatalf("migrate to version %d: %v", version, err)
	}
	assertMigrationVersion(t, provider, version)
}

func assertMigrationVersion(t *testing.T, provider *goose.Provider, want int64) {
	t.Helper()
	got, err := provider.GetDBVersion(t.Context())
	if err != nil || got != want {
		t.Fatalf("migration version = %d, err = %v; want %d", got, err, want)
	}
}

func finishMigrations(t *testing.T, provider *goose.Provider) {
	t.Helper()
	if _, err := provider.Up(t.Context()); err != nil {
		t.Fatalf("apply all remaining migrations: %v", err)
	}
	assertMigrationVersion(t, provider, 22)
	results, err := provider.Up(t.Context())
	if err != nil || len(results) != 0 {
		t.Fatalf("repeating migrations must be a no-op: applied = %d, err = %v", len(results), err)
	}
}

func executeMigrationSQL(t *testing.T, db *sql.DB, query string, args ...any) {
	t.Helper()
	if _, err := db.ExecContext(t.Context(), query, args...); err != nil {
		t.Fatalf("fixture or assertion SQL failed: %v", err)
	}
}

func TestPostgresMigrationsCleanInstall(t *testing.T) {
	db, provider := migrationDatabase(t)
	finishMigrations(t, provider)
	assertUUIDColumns(t, db)

	// Exercise the final schema instead of only trusting Goose's version table.
	var userID string
	if err := db.QueryRowContext(t.Context(), `
		INSERT INTO users (email, password, first_name, last_name)
		VALUES ('fresh@example.invalid', 'synthetic-hash', 'Fresh', 'User')
		RETURNING id::text`).Scan(&userID); err != nil {
		t.Fatalf("insert user with UUID default: %v", err)
	}
	if _, err := uuid.Parse(userID); err != nil {
		t.Fatalf("generated user ID is not a UUID: %v", err)
	}
	executeMigrationSQL(t, db, `INSERT INTO organizations (name, created_by) VALUES ('Fresh organization', $1)`, userID)

	var plans int
	if err := db.QueryRowContext(t.Context(), `SELECT count(*) FROM plans WHERE code IN ('starter', 'pro', 'business')`).Scan(&plans); err != nil || plans != 3 {
		t.Fatalf("seeded plans = %d, err = %v; want 3", plans, err)
	}
	var columns int
	if err := db.QueryRowContext(t.Context(), `
		SELECT count(*) FROM information_schema.columns
		WHERE table_schema = 'public' AND table_name = 'logs'
		AND column_name IN ('section', 'auth_result')`).Scan(&columns); err != nil || columns != 2 {
		t.Fatalf("final log columns = %d, err = %v; want section and auth_result", columns, err)
	}
}

const legacyMigrationFixture = `
INSERT INTO organizations (id, name, description, employee_count, country, created_at, updated_at)
VALUES
 (101, 'Store Alpha', 'Legacy description', 3, 'SE', '2024-01-02 03:04:05', '2024-02-03 04:05:06'),
 (202, 'Store Beta', NULL, 2, 'NO', '2024-03-04 05:06:07', '2024-04-05 06:07:08');
INSERT INTO users (id, email, password, first_name, last_name, phone, company, role,
                   is_active, email_verified, verification_token, reset_token,
                   reset_token_expiry, last_login, organization_id, created_at, updated_at)
VALUES
 (11, 'alice@example.invalid', 'alice-hash', 'Alice', 'Legacy', '+460001', 'Alpha AB', 'user',
  true, true, NULL, 'reset-alice', '2025-02-03 04:05:06', '2024-06-07 08:09:10', 101,
  '2024-01-02 03:04:05', '2024-02-03 04:05:06'),
 (22, 'bob@example.invalid', 'bob-hash', 'Bob', 'Legacy', NULL, NULL, 'user',
  false, false, 'verify-bob', NULL, NULL, NULL, NULL,
  '2024-03-04 05:06:07', '2024-04-05 06:07:08'),
 (33, 'carol@example.invalid', 'carol-hash', 'Carol', 'Legacy', NULL, 'Beta AS', 'admin',
  true, true, NULL, NULL, NULL, '2024-08-09 10:11:12', 202,
  '2024-05-06 07:08:09', '2024-06-07 08:09:10');
INSERT INTO user_organizations (id, user_id, organization_id, role, joined_at, is_active)
VALUES
 (301, 11, 101, 'admin', '2024-01-02 03:04:05', true),
 (302, 22, 101, 'member', '2024-02-03 04:05:06', false),
 (303, 11, 202, 'member', '2024-03-04 05:06:07', true),
 (304, 33, 202, 'admin', '2024-04-05 06:07:08', true);
INSERT INTO secrets (id, app, name, description, ciphertext, iv, algo, created_by, organization_id, created_at, updated_at)
VALUES
 (401, 'shopify', 'organization-token', 'Encrypted alpha token', decode('00ff1020', 'hex'), decode('01020304', 'hex'), 'AES-256-GCM', '11', 101, '2024-01-02T03:04:05Z', '2024-02-03T04:05:06Z'),
 (402, 'shopify', 'organization-token', NULL, decode('ff00aabb', 'hex'), decode('05060708', 'hex'), 'AES-256-GCM', '11', 202, '2024-03-04T05:06:07Z', '2024-04-05T06:07:08Z'),
 (403, 'analytics', 'personal-token', NULL, decode('aa00ffbb', 'hex'), decode('090a0b0c', 'hex'), 'AES-256-GCM', '22', NULL, '2024-05-06T07:08:09Z', '2024-06-07T08:09:10Z'),
 (404, NULL, 'optional-app-token', 'No app name', decode('10203040', 'hex'), decode('0d0e0f10', 'hex'), 'AES-256-GCM', '33', 202, '2024-07-08T09:10:11Z', '2024-08-09T10:11:12Z');
`

// Resolve relationships through natural keys so this snapshot checks identity
// preservation across the integer-to-UUID conversion, not the new ID values.
const legacySnapshotSQL = `
SELECT jsonb_build_object(
 'users', (SELECT jsonb_agg(to_jsonb(row) ORDER BY email) FROM (
   SELECT u.email, u.password, u.first_name, u.last_name, u.phone, u.company, u.role,
          u.is_active, u.email_verified, u.verification_token, u.reset_token,
          u.reset_token_expiry, u.last_login, u.created_at, u.updated_at, u.deleted_at,
          o.name AS organization
   FROM users u LEFT JOIN organizations o ON o.id = u.organization_id
 ) row),
 'organizations', (SELECT jsonb_agg(to_jsonb(row) ORDER BY name) FROM (
   SELECT name, description, employee_count, ecommerce_domain, industry, company_size,
          website, country, city, address, phone_number, is_active, subscription_plan,
          created_at, updated_at, deleted_at
   FROM organizations
 ) row),
 'memberships', (SELECT jsonb_agg(to_jsonb(row) ORDER BY email, organization) FROM (
   SELECT u.email, o.name AS organization, m.role, m.joined_at, m.is_active
   FROM user_organizations m JOIN users u ON u.id = m.user_id
   JOIN organizations o ON o.id = m.organization_id
 ) row),
 'secrets', (SELECT jsonb_agg(to_jsonb(row) ORDER BY name, organization, email) FROM (
   SELECT s.app, s.name, s.description, s.ciphertext, s.iv, s.algo, s.created_at, s.updated_at,
          u.email, o.name AS organization
   FROM secrets s LEFT JOIN users u ON u.id::text = s.created_by::text
   LEFT JOIN organizations o ON o.id = s.organization_id
 ) row)
)::text`

func legacySnapshot(t *testing.T, db *sql.DB) string {
	t.Helper()
	var snapshot string
	if err := db.QueryRowContext(t.Context(), legacySnapshotSQL).Scan(&snapshot); err != nil {
		t.Fatalf("snapshot legacy data and relationships: %v", err)
	}
	return snapshot
}

func assertLegacySnapshot(t *testing.T, db *sql.DB, want string) {
	t.Helper()
	if got := legacySnapshot(t, db); got != want {
		t.Fatalf("legacy data or relationships changed\nbefore: %s\nafter:  %s", want, got)
	}
}

func assertUUIDColumns(t *testing.T, db *sql.DB) {
	t.Helper()
	for table, columns := range map[string][]string{
		"users":              {"id", "organization_id"},
		"organizations":      {"id"},
		"user_organizations": {"id", "user_id", "organization_id"},
		"secrets":            {"id", "created_by", "organization_id"},
	} {
		for _, column := range columns {
			var dataType string
			if err := db.QueryRowContext(t.Context(), `
				SELECT data_type FROM information_schema.columns
				WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2`, table, column).Scan(&dataType); err != nil || dataType != "uuid" {
				t.Fatalf("%s.%s type = %q, err = %v; want uuid", table, column, dataType, err)
			}
		}
	}
}

func expectPostgresCode(t *testing.T, db *sql.DB, code string, query string) {
	t.Helper()
	_, err := db.ExecContext(t.Context(), query)
	var postgresError *pq.Error
	if !errors.As(err, &postgresError) || string(postgresError.Code) != code {
		t.Fatalf("expected PostgreSQL %s constraint rejection, got %v", code, err)
	}
}

func assertLegacyConstraints(t *testing.T, db *sql.DB) {
	t.Helper()
	expectPostgresCode(t, db, "23505", `
		INSERT INTO user_organizations (user_id, organization_id)
		SELECT user_id, organization_id FROM user_organizations LIMIT 1`)
	expectPostgresCode(t, db, "23505", `
		INSERT INTO secrets (app, name, ciphertext, iv, created_by, organization_id)
		SELECT app, name, ciphertext, iv, created_by, organization_id
		FROM secrets WHERE app = 'shopify' LIMIT 1`)
	expectPostgresCode(t, db, "23503", `
		INSERT INTO user_organizations (user_id, organization_id)
		SELECT uuid_generate_v4(), id FROM organizations LIMIT 1`)
	expectPostgresCode(t, db, "23503", `
		UPDATE users SET organization_id = uuid_generate_v4() WHERE email = 'bob@example.invalid'`)
}

func TestPostgresMigrationsPreserveLegacyData(t *testing.T) {
	db, provider := migrationDatabase(t)
	migrateTo(t, provider, 5)
	executeMigrationSQL(t, db, legacyMigrationFixture)
	before := legacySnapshot(t, db)

	migrateTo(t, provider, 6)
	assertUUIDColumns(t, db)
	assertLegacySnapshot(t, db, before)
	assertLegacyConstraints(t, db)

	migrateTo(t, provider, 7)
	assertLegacySnapshot(t, db, before)
	var inventedOwners int
	if err := db.QueryRowContext(t.Context(), `SELECT count(*) FROM organizations WHERE created_by IS NOT NULL`).Scan(&inventedOwners); err != nil || inventedOwners != 0 {
		t.Fatalf("legacy organization ownership must remain unknown: non-null creators = %d, err = %v", inventedOwners, err)
	}
	expectPostgresCode(t, db, "23503", `UPDATE secrets SET created_by = uuid_generate_v4() WHERE name = 'personal-token'`)
	if _, err := provider.Down(t.Context()); err == nil {
		t.Fatal("irreversible migration 007 unexpectedly rolled back")
	}
	assertMigrationVersion(t, provider, 7)
	assertLegacySnapshot(t, db, before)

	finishMigrations(t, provider)
	assertUUIDColumns(t, db)
	assertLegacySnapshot(t, db, before)
	assertLegacyConstraints(t, db)
}

func TestPostgresMigrationsRejectUnknownSecretCreator(t *testing.T) {
	for _, author := range []string{"99999", "not-a-user", ""} {
		t.Run("author_"+strings.ReplaceAll(author, "-", "_"), func(t *testing.T) {
			db, provider := migrationDatabase(t)
			migrateTo(t, provider, 5)
			executeMigrationSQL(t, db, legacyMigrationFixture)
			executeMigrationSQL(t, db, `UPDATE secrets SET created_by = $1 WHERE id = 403`, author)
			before := legacySnapshot(t, db)
			if _, err := provider.UpTo(t.Context(), 6); err == nil {
				t.Fatal("migration accepted an unknown secret creator instead of rolling back")
			}
			assertMigrationVersion(t, provider, 5)
			assertLegacySnapshot(t, db, before)
			var userIDType string
			if err := db.QueryRowContext(t.Context(), `
				SELECT data_type FROM information_schema.columns
				WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'id'`).Scan(&userIDType); err != nil || userIDType != "integer" {
				t.Fatalf("failed conversion must retain integer IDs: type = %q, err = %v", userIDType, err)
			}
			var remainingAuthor string
			if err := db.QueryRowContext(t.Context(), `SELECT created_by FROM secrets WHERE id = 403`).Scan(&remainingAuthor); err != nil || remainingAuthor != author {
				t.Fatalf("failed conversion changed the original author: got %q, err = %v", remainingAuthor, err)
			}
			var temporaryColumns int
			if err := db.QueryRowContext(t.Context(), `
				SELECT count(*) FROM information_schema.columns
				WHERE table_schema = 'public' AND column_name LIKE 'temp_%'`).Scan(&temporaryColumns); err != nil || temporaryColumns != 0 {
				t.Fatalf("failed conversion left temporary columns: count = %d, err = %v", temporaryColumns, err)
			}
		})
	}
}
