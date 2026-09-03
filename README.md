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
    ├── inspections/        Evening check + the morning room round
    ├── weekend/            WeekendStay, nightly checks, printable roster
    ├── leave_permissions/  Pass rules (who may be given a pass) + history
    ├── reports/            Monthly worksheets (HTML + xlsx), CSV/JSON/ZIP, PDF
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
| Snapshot | `RoomCheckStudentResult` | Copies name, room and class at entry time, so a later room move or rename cannot rewrite that morning's sheet |

There is no `current_room` column on the student. The active `RoomAssignment`
is the single source of truth, guarded by a partial unique constraint.

### The morning round is one pass, not two (Sept 2026 rework)

The separate morning inspection is gone. A teacher opens a floor once and, on
the same screen, rates each room *and* sets every resident's morning status —
`Bent`, `Nincs tanítás`, `Iskolában`, `Orvosnál` and so on. Setting a status
also moves the student's live presence, sourced as `room_check`, so the
presence history stays attributable.

What the old design computed from a 04:00 cutoff is now recorded directly by
the person standing in the room, which is both simpler and more truthful. The
snapshot principle survives where it still matters: each result freezes the
student's name, room number and class at entry time, so a later room move or
rename cannot rewrite that morning's sheet.

`MorningSnapshot`, `MorningSnapshotItem`, `MorningMonth`, the 04:00 cutoff
calculation and the nightly snapshot job were all removed.

**A room added to a floor after that day's session already exists** used to
never get a `RoomCheck` row — the rows were bulk-created once, only when the
session was first opened, and never refreshed afterward. A room created
later that day (a new floor added mid-use was the case that surfaced it —
floor `0` in particular, being falsy in Python, though the actual bug was the
one-time snapshot, not a truthy check) had no row to rate against, so it
silently never appeared on the entry sheet *or* in the monthly worksheet —
while the evening check showed it correctly, since that side never
snapshots rooms at all; it looks up each student's current room fresh on
every request. `sync_room_check_rows()` (`apps/inspections/services/roomcheck.py`)
makes the room list idempotently back-fill on every visit to the floor page
and the index page, not just at session creation. `backfill_room_check_rows`
is a one-off management command for sessions created before this fix.

### Pass rules replace the pass system (Sept 2026 rework)

The dormitory runs its own paper pass system, so this application no longer
issues or expires passes. It records an *attribute* of each student instead —
whether a pass may be given, and by whom:

| Setting | Meaning |
| --- | --- |
| `allowed` | Kaphat kimenőt |
| `teacher_only` | Csak a saját nevelőtanára adhat |
| `denied` | Nem kaphat kimenőt |
| `other` | Egyéb — a free-text note is then required, at the database level |

Read and write scopes differ on purpose: **every** teacher, porter and
director reads the whole dormitory's rules (whoever is on duty is the one being
asked for a pass), while **setting** a rule stays with the student's own
teachers and with management. The rule also rides along as a column in the
presence roster, where staff actually look. Every change is appended to
`PassRuleHistory`.

`LeavePermission`, its history and the daily expiry job were removed.

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

**Two different scopes, deliberately.** Presence is a whole-building concern —
whoever is on duty answers for every floor — so `presence_queryset_for_user`
returns the entire dormitory for any staff role. Access to the student
*record* (guardian contacts, medical notes) stays group-scoped in
`student_queryset_for_user`. A teacher therefore sees all 46 students' status
on the roster but only their own group's records, and a test pins that
difference.

Explicitly tested: student ↛ student B, teacher ↛ other groups, porter ↛ edit
presence, teacher ↛ management-only actions, management ↛ `DELETE_ALL_DATA`.

### Privilege management has the same admin/management ceiling as everywhere else

Admin and management can both edit any account's role and per-capability
overrides at **Adatkezelés → Felhasználók** (`apps/accounts/selectors.py`,
`services.py`) — `MANAGE_USERS` sits on the management role now, not just
admin's `ALL_CAPABILITIES`. The line drawn everywhere else in this app (spec
§3.2: "management should not automatically receive system-owner permissions")
applies here too, enforced the same way as `student_queryset_for_user` scopes
students:

- `users_manageable_by(actor)` — admin manages everyone, including other
  admins and themselves; management manages everyone *except* the admin tier
  and except themselves (self-edit stays out of this screen, so a mistake here
  can't self-lock the account making it — an admin can always fix it).
- `assignable_roles_for(actor)` — the Admin role itself is only assignable by
  an admin; management can promote a teacher to management, but never to
  admin.
- `DELETE_ALL_DATA` cannot be handed out as a one-off grant through this
  screen by anyone, admin included — it only ever comes from the Admin role.

Both checks run at the service layer, not just in the view, and every change
is audited with the before/after capability sets.

**Permanent deletion is a further, admin-only step on top of all that**
(`DELETE_USERS`, `apps/accounts/services.py::delete_user_permanently`) —
archiving and disabling stay the reversible options for management; hard
deletion is not. It refuses four ways before it touches anything: never a
student account (`StudentProfile.user` is `on_delete=PROTECT` precisely so
this can't happen by accident — spec §7 — the operator is pointed at the
existing archive flow instead), never yourself, never the last admin account
standing, and only after typing the account's exact username as confirmation.
`DELETE_USERS` is excluded from the privilege screen's grantable list
alongside `DELETE_ALL_DATA` — it only ever comes from the Admin role itself.
Every other FK from historical data to `User` (presence events, room checks,
audit entries, weekend reviews, ...) is `SET_NULL`, so deleting an account
clears "who did this" attribution on that history without touching the
history itself; a `Teacher` profile cascades away with its account, since it
carries no history of its own.

### Concurrency on inspections (spec §18)

The legacy timestamp check was replaced with real locking. Every state change
re-reads the session under `select_for_update()`; the `locked_by`/`locked_at`
columns are an advisory "who is editing this floor" indicator with an
expiry, not the correctness mechanism. A per-session/per-student unique
constraint means a lost race produces an update, never a duplicate.

### Weekends are checked at night only (Sept 2026 rework)

The Saturday-morning and Sunday-morning rounds were dropped: what matters over
a weekend is who actually slept in the building. Two checks remain — **Péntek
éjszaka** and **Szombat éjszaka** — and each one only lists the students whose
approved stay covers that night. A data migration deletes the retired sessions.

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
are replaced with `[nem naplózva]` before an audit row is written — the trail
still shows *that* a field changed. Imported temporary passwords are shown
once in the UI and never logged.

### The UI is Hungarian throughout (Sept 2026 rework)

Every page a user actually reads is Hungarian: navigation, buttons, form
labels, validation and permission-refusal messages, the audit trail, and the
ad-hoc report tables. Two places are deliberately not translated because
translating them would be wrong, not because they were missed:

- **CSV import column contracts** (`number, floor, capacity, is_active,
  notes` on the room importer; `username, email, full_name, ...` on the
  teacher importer). These are literal header names the parser matches
  against an uploaded file — the same category as a JSON API's field names.
  Translating the displayed hint without translating what the parser accepts
  would just make the two disagree.
- **CSV/JSON export column headers**. The *on-screen* HTML table for every
  ad-hoc report is translated (`apps/reports/templatetags/report_tags.py::column_label`),
  but the downloaded CSV/JSON keeps the original English key as its column
  header, since that is a machine-readable contract another program may
  already be parsing.

Two spots needed more than a string swap:

- **The audit trail's "Változás" column** used to print a raw Python dict —
  `{'rating': 4, 'problems': ''}`. Since every model field already carries a
  Hungarian `verbose_name` (added for this pass), `apps/audit/templatetags/audit_tags.py::format_change`
  renders `"értékelés: 4; hibák: "` instead, resolving each key against the
  audited model's own field definitions. Ad-hoc payloads that were never model
  fields (progress counters, import summaries) get a small hand-written label
  map with the same fallback-to-raw-key safety net.
- **The capability list on the profile page** used to show raw codes like
  `students.view_sensitive`. `apps/accounts/capabilities.py::CAPABILITY_LABELS`
  maps every capability to a Hungarian phrase for display; the underlying code
  is unchanged everywhere else, so nothing about authorization itself moved.

A page was checked as every role that can reach it (admin, management,
teacher, porter, student) rather than once as an administrator, since several
of these strings only render for a specific role or a specific error path.

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
| `ensure_calendar_days_task` | 00:05 daily | fills gaps, never overwrites operator edits |
| `release_stale_locks_task` | every 10 min | clears edit locks abandoned by a closed tab |

The morning-snapshot and leave-expiry jobs were removed in the September 2026
rework: the morning round is recorded by hand during the room check, and pass
rules are an attribute rather than something that expires.

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

218 tests, PostgreSQL-backed (never SQLite — the schema depends on Postgres
constraints):

```bash
docker compose exec web pytest
```

- **Unit** — presence transitions and the self-service switch, weekend date
  arithmetic, room capacity, assignment overlap, state transitions, pass-rule
  transitions, monthly grid shape (month lengths, leap years, weekend columns,
  floor sections, room grouping and the united room cells)
- **Integration** — student self-service over HTTP (both sides of the switch),
  evening inspection, the merged morning round, weekend approval and nightly
  checks, pass-rule changes, change-request approval, .xlsx generation read
  back with openpyxl
- **Authorization** — every case the spec names, at both the service and the
  view layer
- **Import** — malformed CSV, duplicates, conflicts, overwrite choices,
  transactional rollback
- **Localization** — every "new record" form renders its Hungarian labels
  (a regression guard for a keyword-only-argument bug that would otherwise
  crash the page instead of showing English), the audit trail translates
  known and unknown payload shapes without crashing, report column headers,
  capability labels, and a per-role sweep across every page a real user can
  reach for leftover English vocabulary

---

## Reports

Viewing reports (`VIEW_REPORTS`) is granted to teachers, not just management —
teachers are the ones actually reading the monthly worksheets floor by floor.
Exporting (`EXPORT_DATA` — CSV, JSON, `.xlsx`) stays management-only, and each
ad-hoc report is scoped the same way the underlying module already is: the
weekend report, for instance, uses `weekend_stay_queryset_for_user`, so a
teacher without broader weekend access sees only their own groups' stays here
too, not the whole dormitory's.

The primary reports are two **monthly worksheets**, shaped like the paper
sheets the dormitory already keeps. Both are **split by floor** — a floor's
teacher gets their floor's sheet — with one row per room or student and one
column per day of the month:

| Report | Rows | Cells |
| --- | --- | --- |
| Szobarend | rooms | the tidiness mark, 1–5 |
| Esti jelenlét | students, grouped by room | `+` if the student was in at the moment of the check, `-` if not, blank if there was no check |

Deliberately spare: the tidiness sheet carries room numbers and marks and
nothing else (no floor column — the section heading says it, no averages), and
the presence sheet has no inside/other/checked totals. On the presence sheet a
room number is written once and **united down over its residents** — a
`rowspan` on the printed page, a merged range in the spreadsheet — so there are
no stray blank cells under it.

Each renders as a print-optimised A4-landscape page — one table per floor, each
floor starting a new page, weekend columns shaded, labels frozen while the days
scroll — and downloads as a real `.xlsx` via openpyxl, **one worksheet per
floor**, with a frozen header, merged room cells and repeated print titles. Both outputs are built
from the same `MonthlyGrid`, so the printed page and the spreadsheet can never
disagree. Exports are audited.

A student who has moved out mid-month still has results, so they land in a
trailing `Nincs szoba` section rather than vanishing from the sheet.

The older ad-hoc reports (presence, occupancy, room checks, pass rules and
their histories) remain available as CSV and JSON underneath.

## UI

Mobile-first, because students use this from a phone (spec §51): the student
dashboard is **one** oversized presence button, a status card, and their room,
pass and weekend state, with confirmation on the actions that matter.

Presence self-service is a *switch*, not two independent buttons. Inside, only
`KIMENTEM` is offered; outside, only `BEJÖTTEM`. The service layer enforces the
same rule, so a stale page or an impatient double tap is refused with a plain
message instead of recording a second event — and the permission check runs
before the state check, so poking at another student's endpoint says "not
yours" rather than leaking where they are. Staff screens
widen to a sidebar layout on tablets and desktops; inspection entry is a
keyboard/tap-friendly one-row-per-student table with progress feedback. The
weekend roster prints as A4 landscape and downloads as a real PDF (reportlab),
falling back to the print view if reportlab is unavailable.

---

## Known gaps

- **Data migration from the old WordPress database is not implemented.** The
  spec (§63) describes it as a later step; the domain model is ready for an
  extract/transform/validate import command, but no such command exists yet.
- The September 2026 rework **dropped the old morning-snapshot and
  leave-permission tables**, so data held in them is gone from the live
  database. `restore-point-1` (see RESTORE.md) still has it.
- Presence *editing* was widened to the whole dormitory along with viewing, on
  the reasoning that a duty teacher must be able to correct any student's
  status. Only viewing was explicitly requested; re-narrowing it is a one-line
  change in `apps/presence/services.py::can_change_presence_of`.
- A student who is out cannot change their stated reason without marking
  themselves back in first — a consequence of the switch being strict.
- Email delivery (password reset) needs real SMTP settings in production; the
  development stack prints to the console.
- The confirmation dialog is covered by manual browser verification, not by an
  automated test — there is no JS test runner in this project. Adding one for
  a ~180-line script did not seem worth the dependency, but it does mean the
  dialog is not regression-protected.
