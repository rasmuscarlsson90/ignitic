# PostgreSQL migration safety

The backend applies Goose migrations automatically from `database/migrations`
when started from `apps/backend`. PostgreSQL 16 is used by Compose and migration
regression tests. Test the real SQL as well as the SQLite handler tests before
deploying schema changes.

## Legacy integer IDs to UUIDs

Migration 006 originally used two constraint names that do not exist after
migrations 001–005:

| Relationship | Created by | Actual constraint | Previously assumed name |
| --- | --- | --- | --- |
| `users.organization_id` | 002 | `fk_users_organization` | `users_organization_id_fkey` |
| `secrets.organization_id` | 004 | `fk_secrets_organization` | `secrets_organization_id_fkey` |

The two `user_organizations_*_fkey` names were already correct. Correcting just
the names was insufficient: the old 006 generated unrelated UUIDs for foreign
keys, and 007 dropped and recreated all four core tables.

006 now locks the affected tables in its Goose transaction and creates one
temporary mapping per legacy user and organization. Existing references use
those mappings. Business fields, encrypted secret bytes, timestamps, nullable
organization references, membership uniqueness and foreign key checks survive.
Only identifiers change representation. Temporary mappings disappear on commit.
Allow for the exclusive lock and table rewrite when scheduling an upgrade.

Legacy `secrets.created_by` was unconstrained text. Values must exactly match an
existing legacy user's numeric ID as text. If any author is unknown or malformed,
006 raises an error before replacing columns; the transaction and migration
version remain unchanged. Review such rows with their actual owner before
retrying. The migration never deletes them or guesses their owner.

007 adds creator relationships in place. An old organization has no recorded
creator, so `organizations.created_by` remains nullable rather than inventing
ownership. New application-created organizations continue to supply a creator.
013 now has a correctly delimited Goose Down section, so applying later log
migrations no longer fails SQL parsing.

## Existing installations and rollback

Take a verified database backup before deploying. Test the upgrade on a restored
copy first. 006 and 007 deliberately reject Down migrations: automatically
reconstructing legacy identifiers or removing creator information is not safe.
Restore a pre-upgrade backup if a rollback is required.

These changes repair installations that have not successfully applied 006/007,
including a transactionally failed 006 at version 5. Goose does not rerun already
applied migrations. An installation that ran the old destructive 007 needs a
separate audit and any available backup; this patch cannot recover deleted data.
Do not reset its migration history or database to force historical migrations
to run again.

## Regression checks

The PostgreSQL tests use `MIGRATION_TEST_DATABASE_URL` as an administrative
connection and create randomly named temporary databases. The role must have
`CREATEDB` and permission to install `uuid-ossp`. Tests drop only databases they
created; they never migrate or reset the database in the supplied URL.

```bash
cd apps/backend
# Supply the URL securely in your shell, without committing or logging it.
go test -tags=integration ./database
go test ./tests/...
```

The regression suite covers a completely fresh installation, populated legacy
records and relationships through 006 and the remaining migrations, repeatable
Up calls, rejected orphan authors and transactional rollback. CI provides an
isolated PostgreSQL 16 service. Backend handler tests block external HTTP through
a closed loopback proxy so test email/invitation paths cannot contact vendors.
