# Deák Koli — Kollégiumi Management System

A Django rebuild of the dormitory management system described in
*Deák Koli Management System - Django Rebuild Specification.md*.

The old WordPress/PHP application was treated as the source of **business
requirements only**. Nothing of its architecture or schema was carried over.

---

## Quick start

```bash
cp .env.example .env
```

Fill in `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` (both are required — the
stack refuses to start without them), then:

```bash
docker compose up -d --build
```

The app is at <http://localhost>. Migrations, `collectstatic` and the
reference-data seed run automatically on start.

nginx publishes port 80 by default. Docker Desktop binds it without `sudo`; if
something else already holds port 80 on the host, set `HTTP_PORT` in `.env`
(e.g. `HTTP_PORT=8080`) and add the matching origin to
`DJANGO_CSRF_TRUSTED_ORIGINS`.

Load a development dataset:

```bash
docker compose exec web python manage.py seed_reference_data --school-year 2026/2027
```

```bash
docker compose exec web python manage.py create_demo_data --students 45
```

Demo accounts (password `demo-password-2025`): `admin`, `vezeto`, `portas`,
`tanar1`–`tanar3`, `diak001`… `create_demo_data` refuses to run with
`DEBUG=False` unless forced.

Run the tests:

```bash
docker compose exec web pytest
```

---

## Stack

| Piece | Choice |
| --- | --- |
| Web | Django 5.2, server-rendered templates |
| Database | PostgreSQL 16 |
| Broker / cache | Redis 7 |
| Background jobs | Celery worker + Celery Beat |
| App server | Gunicorn behind nginx |
| Frontend | Django templates + ~100 lines of vanilla JS |

No REST framework and no SPA: there is no API consumer yet, so adding one
would only add maintenance surface (spec §67). The small JS layer in
`static/js/app.js` upgrades four interactions — the mobile drawer, the
confirmation dialog, inline row saves and the polling live roster — and every
page works without it.

**No native `window.confirm` anywhere.** Browsers and webviews are free to
suppress native dialogs (embedded webviews, "prevent this page from creating
additional dialogs", managed and automated browsers), and a suppressed
`confirm()` returns `false` — which silently cancelled the action and made the
button look broken. Confirmation is drawn in the page with `<dialog>` instead,
with a plain-overlay fallback.

## Layout

```
backend/
├── config/settings/{base,development,test,production}.py
├── config/{urls,celery,wsgi,asgi}.py
└── apps/
    ├── accounts/           User, roles, capabilities, auth flows
    ├── core/               SchoolYear, SystemModule, SystemSetting
    ├── audit/              AuditLog + central recording with redaction
    ├── people/             Group, Teacher (+ CSV import/export)
    ├── students/           StudentProfile, StudentChangeRequest
    ├── rooms/              Room, RoomAssignment (+ CSV import/export)
    ├── presence/           StatusType, StudentPresence, PresenceEvent
    ├── dormcalendar/       CalendarDay
    ├── inspections/        Evening / Morning / Room checks
    ├── weekend/            WeekendStay, WeekendCheck, printable roster
    ├── leave_permissions/  Current permission + history + expiry job
    ├── reports/            Query layer, CSV/JSON/ZIP export, PDF
    └── portal/             Dashboards and capability-driven navigation
```

Apps are domain-sized, not table-sized (spec §52).

---

## The architectural decisions that matter

### Current state vs. history vs. snapshots (spec §70, §71)

Three deliberately separate things:

| Kind | Example | Rule |
| --- | --- | --- |
| Current state | `StudentPresence`, `LeavePermission`, active `RoomAssignment` | Overwritten freely |
| History | `PresenceEvent`, `LeavePermissionHistory`, `RoomCheckHistory` | Append-only; `PresenceEvent.save()` raises if you try to update one |
| Snapshot | `MorningSnapshotItem` | Copies name, room, floor and class at generation time, so a later room move or rename cannot rewrite the past |

There is no `current_room` column on the student. The active `RoomAssignment`
is the single source of truth, guarded by a partial unique constraint.

### The morning cutoff rule (spec §21)

The one business rule the spec singles out lives in a pure function,
`apps/inspections/services/morning.py::calculate_morning_result`, and is
covered by a truth table plus midnight/04:00 edge-case tests:

| evening result | state at cutoff | morning result |
| --- | --- | --- |
| inside | inside | `PRESENT` |
| inside | outside | `LEFT_OVERNIGHT` |
| outside | inside | `RETURNED_LATE` |
| outside | outside | `ABSENT` |
| not checked | either | from the cutoff state alone |
| either | no history | falls back to the evening result |
| not checked | no history | `UNKNOWN` |

**Documented ambiguity** (spec §76.24): the legacy system never defined what
happens when a status changes *exactly* at 04:00. This implementation makes
the cutoff **inclusive** — a change stamped 04:00:00 counts as before it. The
choice is stated in the module docstring and pinned by a test rather than left
implicit.

The state at the cutoff is derived from the event log, not from the student's
current status, so re-generating a snapshot months later yields the same
answer.

### Two-layer authorization (spec §57, §58)

Every protected action passes two independent checks:

1. **Capability** — `user.has_capability(Capability.EDIT_PRESENCE)`. Roles map
   to capability sets in `apps/accounts/capabilities.py`, with per-user
   `extra_capabilities` / `revoked_capabilities` on top.
2. **Object scope** — `student_queryset_for_user(user)` and friends. A teacher
   reaches only their groups' students; a student reaches only themselves.

A capability check alone is never sufficient for a record the user supplied an
id for. Views load objects *through* the scoped queryset, so an out-of-scope id
returns 404 rather than leaking existence. Field-level rules sit on top:
teachers may write a three-field whitelist directly, and anything else becomes
a `StudentChangeRequest` for management to approve.

Explicitly tested: student ↛ student B, teacher ↛ other groups, porter ↛ edit
presence, teacher ↛ management-only actions, management ↛ `DELETE_ALL_DATA`.

### Concurrency on inspections (spec §18)

The legacy timestamp check was replaced with real locking. Every state change
re-reads the session under `select_for_update()`; the `locked_by`/`locked_at`
columns are an advisory "who is editing this floor" indicator with an
expiry, not the correctness mechanism. A per-session/per-student unique
constraint means a lost race produces an update, never a duplicate.

### Constraints in the database, not just in Python (spec §53)

- one active school year (partial unique index)
- one active room assignment per student (partial unique index)
- one weekend stay per student per weekend
- guest XOR student on a weekend stay; at least one night booked
- room capacity ≥ 1, room-check rating within 1–5, valid month, sane date ranges
- one result per student per inspection session

Two tests deliberately assert that PostgreSQL — not just the service layer —
rejects a second active assignment and a second active school year.

### Modules are enforced, not just hidden (spec §41)

`require_module(...)` guards every view of a switchable module, so disabling
one returns 403 even if a URL is typed directly. Navigation hiding is a
convenience on top. Historical data is never touched by a toggle, and the
toggle is audited — all three are covered by tests.

### Maintenance is deliberately hard to fire (spec §49)

Destructive maintenance requires *all* of: the `DELETE_ALL_DATA` capability
(administrators only — management does not get it), the
`ALLOW_DESTRUCTIVE_MAINTENANCE` environment flag, and a typed confirmation
phrase. Even then it clears only operational inspection data; students and
rooms are archived, never dropped. A refused attempt is itself audited.

### Static assets are versioned, not just cached

Production uses `ManifestStaticFilesStorage`, so `app.css` is served as
`app.00a92372539c.css`. nginx matches that hash pattern and serves those files
`public, max-age=31536000, immutable`; everything else under `/static/` gets
`no-cache` and revalidates. Plain filenames behind a long `max-age` would
strand browsers on stale CSS and JS after every deploy until it expired.
`collectstatic` must therefore run on each deploy — the compose `web` command
does it before gunicorn starts.

### Audit vs. domain history (spec §46, §47)

`AuditLog` answers "who changed what"; domain history tables answer "what
happened to this student". They are not merged into one generic table.
Sensitive values (medical notes, guardian contacts, anything password-like)
are replaced with `[redacted]` before an audit row is written — the trail
still shows *that* a field changed. Imported temporary passwords are shown
once in the UI and never logged.

### Validated CSV import (spec §44, §45)

Rooms and teachers share a two-phase pipeline: **parse → validate → preview
with conflicts → operator chooses what to overwrite → apply in one
transaction**. Nothing touches the database before the apply step, and a
failure partway through rolls the whole import back (tested). Row-level errors
are reported per line; encodings, delimiters, row caps and file-size caps are
handled. Teacher accounts get generated temporary passwords with a forced
password change — passwords are never read from a CSV.

---

## Scheduled jobs

Configured in `config/celery.py`; all idempotent, all retry-safe (spec §61):

| Task | Schedule | Notes |
| --- | --- | --- |
| `generate_morning_snapshot_task` | 05:00 daily | after the 04:00 cutoff; running twice creates nothing extra |
| `expire_leave_permissions_task` | 00:10 daily | only `ACTIVE` rows with a past expiry; writes history + audit |
| `ensure_calendar_days_task` | 00:05 daily | fills gaps, never overwrites operator edits |
| `release_stale_locks_task` | every 10 min | clears edit locks abandoned by a closed tab |

---

## Security

`manage.py check --deploy` passes clean under production settings. In place:
CSRF everywhere, Django password hashing with a 10-character minimum, forced
password change for new accounts, login throttling per username/IP,
`HttpOnly`/`SameSite`/`Secure` cookies, HSTS, `X-Frame-Options: DENY`,
nosniff, ORM-only queries (no raw SQL anywhere), size- and type-checked
uploads, and an audit trail on sensitive actions. Production settings raise
`ImproperlyConfigured` at import time if `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS` or `POSTGRES_PASSWORD` is missing, so a misconfigured
deploy fails immediately instead of running insecurely. The container runs as
an unprivileged user. `.env` is gitignored; no secret has a usable default.

---

## Tests

115 tests, PostgreSQL-backed (never SQLite — the schema depends on Postgres
constraints):

```bash
docker compose exec web pytest
```

- **Unit** — presence transitions, morning cutoff (truth table + midnight/04:00
  edges), weekend date arithmetic, permission expiry, room capacity,
  assignment overlap, state transitions
- **Integration** — student self-service, evening inspection over HTTP,
  snapshot generation, weekend approval and checks, leave permission
  lifecycle, change-request approval
- **Authorization** — every case the spec names, at both the service and the
  view layer
- **Import** — malformed CSV, duplicates, conflicts, overwrite choices,
  transactional rollback

---

## UI

Mobile-first, because students use this from a phone (spec §51): the student
dashboard is two oversized presence buttons, a status card and their room and
weekend state, with confirmation on the actions that matter. Staff screens
widen to a sidebar layout on tablets and desktops; inspection entry is a
keyboard/tap-friendly one-row-per-student table with progress feedback. The
weekend roster prints as A4 landscape and downloads as a real PDF (reportlab),
falling back to the print view if reportlab is unavailable.

---

## Known gaps

- **Data migration from the old WordPress database is not implemented.** The
  spec (§63) describes it as a later step; the domain model is ready for an
  extract/transform/validate import command, but no such command exists yet.
- Morning monthly grouping (`MorningMonth`, §24) exists as a model and groups
  snapshots, but has no dedicated management screen — the spec said to keep it
  only "if it is actually used", so the data structure is preserved and the UI
  is deferred until that is confirmed.
- Room-inspection student results are recorded per room from the floor view;
  there is no separate bulk-entry screen for them.
- Email delivery (password reset) needs real SMTP settings in production; the
  development stack prints to the console.
- The confirmation dialog is covered by manual browser verification, not by an
  automated test — there is no JS test runner in this project. Adding one for
  a ~180-line script did not seem worth the dependency, but it does mean the
  dialog is not regression-protected.
