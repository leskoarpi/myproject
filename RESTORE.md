# Restore points

## restore-point-1 — before the September 2026 requirements rework

Taken before reworking student presence, morning/room checks, the pass system,
weekend checks, presence visibility and the reports.

| Piece | Where |
| --- | --- |
| Code | git tag `restore-point-1` (commit `20046d7`) |
| Database | `backups/db-restore-point-1.sql` (pg_dump, `--clean --if-exists`) |

`backups/` is gitignored — the dump stays on this machine only, and it contains
personal data, so treat it like production data.

### Roll the code back

```bash
git reset --hard restore-point-1
```

Then rebuild and restart:

```bash
docker compose up -d --build
```

### Roll the database back

This drops and recreates every table in the dump.

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < backups/db-restore-point-1.sql
```

### Roll back both

Do the code first, then the database, then restart the stack — the schema in
the dump matches the code at that tag, not the current code.
