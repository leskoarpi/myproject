# **Deák Koli Management System — Django Rebuild Specification**

## **1\. Project objective**

Rebuild the existing **Deák Koli / Kollégiumi Jelenlét** web application as a professional, maintainable Django application.

The existing application is a WordPress \+ custom PHP system. The goal is **not to reproduce its architecture or code**. The goal is to reproduce and improve its **business functionality and user workflows** using modern software engineering practices.

### **Target stack**

Use:

* **Python**  
* **Django**  
* **PostgreSQL**  
* **Docker / Docker Compose**  
* Redis where useful  
* Celery for background/scheduled jobs where appropriate  
* Django ORM  
* Django migrations  
* Django authentication/authorization  
* Django forms or Django REST Framework where an API is actually useful  
* Proper automated tests  
* Environment-based configuration  
* Production-grade logging  
* Secure secret management

The application should be designed as a **single coherent Django application**, not as a collection of independent PHP-style pages.

Do not copy the existing WordPress database structure literally.

Instead, derive a clean relational domain model from the existing business requirements.

---

# **2\. What the application is**

The application is a **student dormitory management system**.

Its primary purpose is to allow dormitory staff and students to manage and monitor:

1. Students  
2. Rooms  
3. Room assignments  
4. Student presence  
5. Presence history  
6. Evening presence checks  
7. Morning checks  
8. Room/cleanliness inspections  
9. Weekend stays  
10. Weekend presence checks  
11. Leave permissions  
12. Teachers and groups  
13. Student change requests  
14. Calendar/school-year configuration  
15. System modules  
16. Reports and logs  
17. Audit history  
18. Data export/import  
19. Administrative maintenance

The application has multiple user roles with different permissions.

---

# **3\. User roles**

There are five primary roles in the existing system.

## **3.1 Administrator**

Full access.

Can:

* manage all data  
* manage users  
* manage roles/permissions  
* manage rooms  
* manage students  
* manage teachers  
* manage groups  
* manage calendar  
* manage statuses  
* configure modules  
* perform inspections  
* reopen/close inspections  
* manage weekend stays  
* manage leave permissions  
* review student change requests  
* import/export data  
* view logs  
* perform maintenance  
* perform destructive data-reset operations

---

## **3.2 Management / Leadership**

Very high-level administrative access.

Generally can:

* manage/view operational data  
* manage students  
* manage rooms  
* manage teachers  
* manage groups  
* perform/review inspections  
* manage weekend stays  
* manage leave permissions  
* review changes  
* export data  
* access management functions

Should not automatically receive system-owner/destructive permissions unless explicitly granted.

---

## **3.3 Teacher / Educator**

Teachers primarily manage students assigned to their groups.

Can generally:

* view students  
* edit students  
* view/edit presence  
* view presence history  
* perform evening checks  
* perform morning checks  
* perform room checks  
* view room-check history  
* participate in weekend-stay management  
* review weekend-stay requests  
* manage leave permissions  
* review student change requests

Teacher access must be **scope-aware**.

A teacher should not automatically have unrestricted access to every student.

The application should support:

Teacher  
   ↓  
Groups  
   ↓  
Students

A teacher normally operates on students belonging to their assigned groups.

There is an additional concept where management can grant a teacher broader weekend-stay editing permissions.

---

## **3.4 Porter / Reception**

Very limited operational access.

Primary responsibility:

* view current student presence

The porter should **not** have permission to modify student presence or perform administrative inspections unless explicitly granted.

---

## **3.5 Student**

Students can:

* view their own profile  
* view their current room  
* view relevant room-check information  
* change their own presence/status  
* register for weekend stays  
* view their own leave permission/status  
* change their own password

Students must never be able to access another student's private information.

---

# **4\. Authentication**

Use Django's authentication system.

Do not reproduce WordPress authentication.

Requirements:

* username/email-based login as appropriate  
* secure password hashing  
* secure sessions  
* logout  
* password change  
* optional forced password change for newly created accounts  
* password reset flow  
* CSRF protection  
* login throttling/rate limiting where appropriate  
* secure cookie configuration  
* configurable session lifetime

The current system forces newly created users to change their password. Preserve this functionality.

---

# **5\. Core domain model**

The new system should have a clean relational schema.

Suggested conceptual entities:

User  
Role  
Permission

Student  
Teacher  
Group

Room  
RoomAssignment  
SchoolYear

StatusType  
StudentStatus  
PresenceEvent

CalendarDay

EveningCheckSession  
EveningCheckResult

MorningSnapshot  
MorningSnapshotItem  
MorningMonth

RoomCheckSession  
RoomCheck  
RoomCheckStudentResult  
RoomCheckHistory

WeekendStay  
WeekendCheckSession  
WeekendCheck

LeavePermission  
LeavePermissionHistory

StudentChangeRequest

SystemModule  
SystemSetting

AuditLog

Do not necessarily implement these exact names. The important requirement is preserving the domain concepts.

---

# **6\. Students**

A student represents a dormitory resident.

Existing student data includes:

* full name  
* education/student ID  
* birth date  
* school name  
* school class  
* phone  
* address  
* guardian name  
* guardian phone  
* guardian email  
* emergency contact name  
* emergency contact phone  
* medical notes  
* staff notes  
* current room  
* move-in date  
* move-out date  
* active/archived state

The new application should separate:

### **Authentication identity**

from:

### **Student profile**

For example:

User  
  1-to-1  
StudentProfile

Do not store all student information directly on the Django `User`.

---

# **7\. Student lifecycle**

Students can be:

* active  
* archived

Students can have:

* move-in date  
* move-out date

Archiving should normally be preferred over hard deletion.

Historical records must remain valid after a student leaves.

Student records should therefore not be physically deleted during normal operation.

---

# **8\. Rooms**

A room has:

* room number  
* floor  
* capacity  
* active/inactive status

Room number is unique.

Existing UI allows common capacities:

* 2  
* 4  
* 6

but the new system should not hard-code these values unnecessarily.

Rooms can be imported/exported via CSV.

Existing behavior automatically derives the floor from the first digit of the room number. In the new system, **prefer explicit floor data**, but optionally retain automatic floor inference as a convenience.

---

# **9\. Room assignments**

Students can be assigned to rooms.

Assignments are historical.

An assignment contains:

* student  
* room  
* school year  
* start date  
* end date  
* active state  
* audit information

Important:

Do not treat `current_room` as the sole source of truth.

The new system should derive current assignment from assignment history where possible.

Example:

Student  
 ├── RoomAssignment 2025/26 → Room 201  
 └── RoomAssignment 2026/27 → Room 305

This preserves historical room occupancy.

The system should enforce sensible constraints such as:

* a student should not have two overlapping active assignments  
* room occupancy should not exceed capacity unless explicitly allowed  
* an assignment must belong to a valid school year

---

# **10\. School years**

The application tracks school years.

A school year contains:

* name  
* start date  
* end date  
* active flag

Only one school year should normally be active.

Use proper database constraints/business validation.

---

# **11\. Student presence**

Presence is one of the core features.

A student has a **current presence status**.

Default status types currently include:

inside       \= Bent  
outside      \= Kint  
school       \= Iskolában  
doctor       \= Orvosnál  
night\_leave  \= Éjszakai kimenő  
other        \= Egyéb

The exact terminology should be configurable rather than deeply hard-coded.

---

# **12\. Student self-service presence**

Students have two main actions.

## **Return**

Bejöttem

sets the student to:

inside

and records a presence event.

## **Leave**

Kimentem

opens a choice of reason:

* Kimenő  
* Hazament  
* Orvoshoz  
* Iskolába  
* Éjszakai kimenő  
* Egyéb / custom note

The system records:

* previous status  
* new status  
* reason  
* timestamp  
* source  
* acting user

A student should only be able to change their own presence.

---

# **13\. Presence history**

Every status change should produce an immutable historical event.

Conceptually:

PresenceEvent  
\----------------  
student  
old\_status  
new\_status  
reason  
source  
created\_at  
created\_by

This is different from current status.

Current status answers:

> Where is the student now?

History answers:

> What happened over time?

Do not overwrite historical events.

---

# **14\. Presence sources**

The existing system distinguishes sources of status changes.

The new system should preserve this concept.

Possible sources:

student  
teacher  
porter  
admin  
evening\_check  
morning\_check  
room\_check  
system

Use a controlled enum/choice field rather than arbitrary strings if appropriate.

---

# **15\. Calendar**

The system has a dormitory calendar.

Each date can contain:

* date  
* weekday  
* whether evening checking is required  
* status

The calendar is used by inspections.

The new application should generate calendar information instead of storing redundant values like:

year  
month  
day  
weekday  
weekday\_name

unless there is a strong reporting reason.

Prefer:

date

and derive other values.

---

# **16\. Evening presence inspection**

This is a major operational workflow.

Staff perform an evening inspection, normally organized by floor.

Conceptually:

Calendar day  
     ↓  
Evening inspection  
     ↓  
Floor 1  
Floor 2  
Floor 3  
...

Each floor has an inspection session.

A session can be:

open  
closed

and supports temporary locking while someone is actively editing it.

The existing system records:

* who started it  
* when it started  
* who closed it  
* when it closed  
* last activity  
* lock owner  
* lock timestamp

The new system should preserve the concurrency requirement but implement it properly.

Use transactions and database locking where appropriate instead of relying solely on timestamps.

---

# **17\. Evening inspection workflow**

Typical workflow:

Select date  
↓  
Select floor  
↓  
Start/open inspection  
↓  
View students assigned to rooms on that floor  
↓  
Mark each student:  
    Bent / Kint / other configured result  
↓  
Save  
↓  
Complete floor  
↓  
Close inspection

The UI should support efficient data entry because staff may check many students consecutively.

---

# **18\. Evening inspection locking**

The existing application attempts to prevent multiple people from simultaneously editing the same floor inspection.

The Django implementation should use proper concurrency control.

Potential approach:

* database transaction  
* `select_for_update()`  
* session ownership  
* lock expiration  
* optimistic/concurrent update detection

Avoid implementing this as a fragile PHP-style timestamp check.

---

# **19\. Morning inspection**

The morning process is related to the previous evening.

The system creates a **morning snapshot**.

A snapshot contains a fixed representation of relevant students and their previous evening state.

Important reason for snapshotting:

Historical morning records should not change simply because:

* a student changes rooms later  
* the student's name changes  
* the student's current status changes  
* room assignments change

Therefore the new system should deliberately distinguish:

### **Current domain data**

from:

### **Historical inspection snapshots**

---

# **20\. Morning snapshot**

A snapshot contains:

* calendar date  
* snapshot date  
* cutoff timestamp  
* generation timestamp  
* status  
* closed timestamp  
* closed by  
* reopening information

Snapshot items contain historical values such as:

* student  
* student name at time of snapshot  
* room number  
* floor  
* class  
* evening result  
* cutoff status  
* calculated result  
* final result  
* reviewed flag  
* reviewer  
* review timestamp  
* note

The new implementation should treat snapshot data as an immutable historical baseline with controlled corrections.

---

# **21\. Morning cutoff**

The existing implementation contains a concept of a cutoff around **04:00**.

The morning system evaluates the student's state around that cutoff to determine the morning result.

This business rule must be explicitly implemented and covered by tests.

Do not bury this logic inside a view.

Create a domain/service function such as:

calculate\_morning\_result(...)

and test edge cases around midnight and 04:00.

---

# **22\. Morning snapshot generation**

The current application generates the morning snapshot automatically using a scheduled job.

In Django:

Use:

Celery \+ Celery Beat

or another robust scheduler.

Do not use Django request handling to generate it opportunistically.

The operation should be:

Previous evening inspections complete  
        ↓  
Scheduled task  
        ↓  
Generate morning snapshot  
        ↓  
Populate snapshot items

It should be idempotent.

Running the job twice must not create duplicate snapshots.

---

# **23\. Morning review**

Staff can review snapshot items.

Each item can have:

* calculated result  
* final result  
* reviewed state  
* reviewer  
* note

A morning inspection can be:

open  
closed

and may be reopened by authorized users.

---

# **24\. Morning monthly grouping**

The existing system contains a `morning_months` concept.

This groups morning snapshots by month and allows monthly operational management.

Preserve the functionality if it is actually used.

---

# **25\. Room inspection**

The system has a separate **room/cleanliness inspection**.

This is distinct from evening presence checking.

A room inspection records:

* room  
* rating  
* problems  
* notes  
* checker  
* timestamp

The rating is numeric in the existing system.

The new system should make the rating scale configurable or use a documented fixed scale.

---

# **26\. Room inspection sessions**

Room inspections are organized by:

date  
floor

A session can be:

open  
closed

and can be reopened by authorized users.

The session tracks:

* created by  
* created at  
* closed by  
* closed at  
* reopened by  
* reopened at

---

# **27\. Room inspection student results**

The room inspection also records student-specific information.

A student result can include:

* student  
* room  
* presence result  
* detail code  
* departure time  
* detail note  
* saved by  
* saved at

This allows a room inspection to simultaneously record:

> room condition \+ which students were present/absent \+ relevant details.

---

# **28\. Room inspection history**

The system retains historical versions of room inspection information.

The new implementation should use proper audit/versioning semantics rather than overwriting records.

At minimum, preserve:

* previous rating  
* previous problems  
* previous notes  
* when saved  
* who saved it

---

# **29\. Weekend stay module**

Students can register for staying in the dormitory over a weekend.

A weekend stay belongs to a weekend starting on Friday.

A request can specify:

* Friday stay  
* Saturday stay  
* request note

The request has a lifecycle such as:

pending  
approved/rejected/etc.

Use explicit state transitions rather than arbitrary strings.

---

# **30\. Weekend stay — student**

A normal student can submit/manage their own weekend stay request.

The system determines:

* student  
* current room  
* group  
* requested days

The student should not be able to submit a request for another student.

---

# **31\. Weekend stay — guest**

Management can create a **guest student** weekend entry.

A guest has:

* name  
* room  
* phone  
* email  
* guardian name  
* guardian phone  
* guardian email  
* Friday stay  
* Saturday stay  
* note

Important business rule:

> A guest student does not consume a normal weekday room allocation.

Do not create a normal room assignment merely because a guest is staying for the weekend.

---

# **32\. Weekend stay approval/review**

Authorized staff can review weekend stay requests.

The system should support:

* pending  
* approved/rejected or equivalent states  
* review note  
* reviewer  
* reviewed timestamp

Do not allow students to approve their own requests.

---

# **33\. Weekend checks**

The weekend module has four operational checks:

Friday evening  
Saturday morning  
Saturday evening  
Sunday morning

Each check records:

* person  
* date  
* check type  
* result  
* note  
* checker  
* timestamp

Results include:

inside  
outside  
pending / unchecked  
---

# **34\. Weekend check sessions**

Each check type/date has a session.

A session can be:

open  
closed

and authorized management can reopen it.

The new implementation should make these state transitions explicit.

---

# **35\. Weekend printable report**

The existing system has an A4 landscape printable weekend report.

It includes:

* room  
* name  
* group  
* Friday stay  
* Friday evening result  
* Saturday morning result  
* Saturday stay  
* Saturday evening result  
* Sunday morning result  
* notes

The Django application should preserve this report.

Prefer generating a proper PDF rather than relying on browser-specific HTML printing if reliable archival/printing is required.

---

# **36\. Teacher/group management**

Teachers belong to groups.

A teacher can have:

* primary group  
* additional groups

This is a many-to-many relationship.

Conceptually:

Teacher ←→ Group

Students are associated with groups/classes as appropriate.

Teachers use these relationships to determine which students they can access.

---

# **37\. Student change requests**

Teachers should not necessarily modify sensitive student records directly.

The existing system supports:

Teacher  
  ↓  
Proposes student changes  
  ↓  
ChangeRequest  
  ↓  
Management reviews  
  ↓  
Approve / reject

A request contains:

* student  
* requesting user  
* proposed changes  
* status  
* requested timestamp  
* reviewer  
* review timestamp  
* review note

The proposed changes should be stored safely.

Prefer structured JSON for the proposed change set rather than an opaque serialized string.

---

# **38\. Leave permissions**

The system has a separate **leave permission** feature.

Each student can have a current permission.

A permission contains:

* student  
* status  
* permission type  
* permission value  
* granted at  
* granted by  
* expiration  
* updated at

There is also a history.

---

# **39\. Leave permission history**

Every permission change creates a historical record.

Record:

* student  
* action  
* previous status  
* previous value  
* new status  
* new value  
* timestamp  
* acting user

History must not be destroyed when the current permission changes.

---

# **40\. Automatic leave expiration**

Leave permissions can expire automatically.

The current system runs a daily scheduled job.

The Django application should use Celery Beat or equivalent.

The task should:

Find active permissions  
where expires\_at \<= now  
↓  
transition them to expired  
↓  
write history/audit entry

The task must be idempotent.

---

# **41\. Module configuration**

The application supports enabling/disabling major modules.

Current modules:

evening\_check  
morning\_check  
room\_checks  
weekend\_stay  
leave\_permissions

When a module is disabled:

1. It should disappear from applicable navigation.  
2. Users should not be able to access its pages.  
3. Its API endpoints should also enforce the disabled state.  
4. Existing historical data should remain intact.

Do not rely only on hiding navigation.

---

# **42\. Dashboard**

The dashboard is role-aware.

It should show only functionality available to the current user.

Students additionally see:

* their name  
* current presence  
* leave permission  
* presence controls  
* relevant personal modules

Staff see operational modules according to permissions.

Management sees administrative functions.

The dashboard should not contain business logic.

Use permission-aware navigation generated from backend capabilities.

---

# **43\. Data management**

Management/admin has a central data area.

It contains:

Rooms  
Students  
Teachers & groups  
Modules

The new system should have proper CRUD interfaces.

Use:

* Django forms  
* validation  
* transactions  
* pagination  
* filtering  
* search  
* bulk operations where appropriate

Do not reproduce giant procedural PHP files.

---

# **44\. Room CSV import/export**

Room management currently supports:

* CSV template download  
* CSV import  
* import preview  
* conflict detection  
* selective overwrite  
* export

Important workflow:

Upload CSV  
↓  
Validate  
↓  
Preview  
↓  
Show conflicts  
↓  
User chooses keep/overwrite  
↓  
Apply inside transaction

Never directly import unvalidated CSV rows into production data.

---

# **45\. Teacher CSV import/export**

Teacher management supports:

* CSV import  
* CSV export  
* import template  
* teacher creation  
* group assignment

The new system should provide a validated import pipeline with:

* row-level validation  
* error reporting  
* preview  
* transactional application  
* duplicate detection

---

# **46\. Logs**

There are multiple kinds of logs.

The new system should distinguish:

### **Domain history**

Example:

PresenceEvent  
LeavePermissionHistory  
RoomCheckHistory

from:

### **Audit log**

Example:

User X changed Student Y

Do not force every kind of history into one generic table.

---

# **47\. Audit logging**

The existing application records:

* user  
* action  
* target type  
* target ID  
* old value  
* new value  
* IP address  
* timestamp

The Django implementation should provide a centralized audit mechanism.

Audit important administrative actions such as:

* student changes  
* room changes  
* assignments  
* permission changes  
* inspection closure/reopening  
* module changes  
* imports  
* exports  
* account changes  
* destructive operations

Sensitive data should not be blindly serialized into audit logs.

---

# **48\. Backup/export**

The existing system supports full data exports.

The new application should provide controlled exports.

Potential formats:

* CSV  
* JSON  
* PDF reports  
* ZIP containing multiple exports

Never include:

* password hashes unnecessarily  
* session tokens  
* secret keys  
* application secrets

Exports should require appropriate permission.

Large exports should potentially run asynchronously.

---

# **49\. Maintenance**

There is an administrative maintenance area.

It currently supports destructive operations such as deleting large categories of data and resetting the system.

The new application should make these operations much safer.

Requirements:

* extremely restricted permission  
* explicit confirmation  
* transaction where possible  
* backup recommendation  
* audit event  
* preferably soft deletion/archival instead of destructive deletion  
* clear distinction between test/development and production

A production administrator should not accidentally be able to destroy the entire database through a normal web page.

---

# **50\. Notifications/live status**

The existing application uses AJAX polling for some live presence/status information.

The Django version can initially use:

HTMX / periodic polling

rather than introducing WebSockets unnecessarily.

If true real-time updates become important later, use Django Channels.

Do not add infrastructure merely because it is fashionable.

---

# **51\. Mobile-first requirements**

Students primarily use the portal from phones.

Therefore:

* responsive design is required  
* large presence buttons  
* simple navigation  
* minimal typing  
* accessible controls  
* touch-friendly controls  
* clear status colors/icons  
* confirmation for important actions  
* fast page loads

Staff inspection interfaces should also work well on tablets/phones.

---

# **52\. Suggested Django architecture**

Prefer domain-oriented Django apps.

For example:

project/  
│  
├── config/  
│   ├── settings/  
│   │   ├── base.py  
│   │   ├── development.py  
│   │   └── production.py  
│   ├── urls.py  
│   ├── celery.py  
│   └── asgi.py  
│  
├── apps/  
│   ├── accounts/  
│   ├── students/  
│   ├── rooms/  
│   ├── presence/  
│   ├── calendar/  
│   ├── inspections/  
│   ├── weekend/  
│   ├── leave\_permissions/  
│   ├── people/  
│   ├── reports/  
│   ├── audit/  
│   └── system/  
│  
├── templates/  
├── static/  
├── tests/  
├── docker/  
├── manage.py  
├── Dockerfile  
├── compose.yml  
└── pyproject.toml

Avoid creating one Django app for every tiny table.

---

# **53\. Database recommendation**

Use:

**PostgreSQL**

Do not use SQLite for the actual application.

Use PostgreSQL features where they materially improve correctness.

Examples:

* foreign keys  
* check constraints  
* unique constraints  
* partial indexes where appropriate  
* transactions  
* row locking  
* JSONB for structured change proposals/configuration where appropriate

The database should enforce important invariants instead of relying entirely on Python validation.

---

# **54\. Important database design principle**

Do **not** directly recreate the old WordPress tables.

The old database has several denormalized or legacy concepts.

For example, the old student record has:

current\_room\_id

while also having:

room\_assignments

The Django version should establish one authoritative relationship.

Similarly, the old calendar stores:

year  
month  
day  
weekday  
weekday\_name

even though these can be derived from the date.

The rebuild should clean these things up.

---

# **55\. Business logic architecture**

Business rules should not live inside templates/views.

Use service/domain functions for important workflows.

For example:

change\_student\_presence(...)  
start\_evening\_check(...)  
save\_evening\_check\_result(...)  
close\_evening\_check(...)  
generate\_morning\_snapshot(...)  
review\_morning\_item(...)  
submit\_weekend\_stay(...)  
approve\_weekend\_stay(...)  
record\_weekend\_check(...)  
grant\_leave\_permission(...)  
expire\_leave\_permissions(...)  
submit\_student\_change\_request(...)  
approve\_student\_change\_request(...)

These functions should be:

* transaction-aware  
* permission-aware  
* testable  
* reusable from web/API/admin interfaces

---

# **56\. State machines**

Several parts of the system have states.

Use explicit choices/state transition logic for:

### **Inspection**

OPEN → CLOSED  
CLOSED → REOPENED/OPEN

### **Weekend request**

PENDING → APPROVED  
PENDING → REJECTED

### **Leave permission**

EMPTY  
ACTIVE  
EXPIRED  
REVOKED

### **Student change request**

PENDING  
APPROVED  
REJECTED

Do not scatter strings such as `"open"`, `"closed"`, `"approved"` throughout the codebase.

---

# **57\. Permissions**

Permissions should be capability-based.

Existing capabilities include concepts equivalent to:

view students  
create students  
edit students  
delete students

view presence  
edit presence  
view presence history

view/manage rooms  
manage calendar  
manage status types

evening inspection  
edit evening inspection  
reopen evening inspection

morning inspection  
edit morning inspection  
reopen morning inspection

room checks  
edit room checks  
reopen room checks  
view room check history  
view own room checks

manage data  
review student changes  
teacher student access

weekend stay  
review weekend stay  
manage weekend stay

manage leave permissions

import data  
export data  
archive data

manage users  
manage settings  
manage maintenance  
delete all data

Use Django's permission system, groups, and/or custom authorization service.

---

# **58\. Object-level authorization**

This is particularly important.

A permission like:

view students

does not necessarily mean:

> view every student.

Teachers need group-based restrictions.

Students need self-only restrictions.

Therefore implement authorization at the **object/queryset level**.

Examples:

student\_queryset\_for\_user(user)

should return only students the user is allowed to see.

Never rely solely on:

if user.has\_perm(...):

after fetching arbitrary objects.

---

# **59\. Security requirements**

The rebuild should substantially improve security over the existing application.

Required:

* CSRF protection  
* secure password hashing  
* secure sessions  
* strict authorization  
* object-level authorization  
* input validation  
* ORM queries instead of string-built SQL  
* parameterized queries where raw SQL is unavoidable  
* secure file upload handling  
* CSV validation  
* rate limiting for authentication  
* secure headers  
* secret values via environment variables  
* no secrets in Git  
* production DEBUG=False  
* secure cookies in production  
* HTTPS in production  
* audit trail for sensitive actions

Medical notes and guardian information should be treated as sensitive personal data.

---

# **60\. Docker architecture**

Recommended initial production-like development stack:

┌───────────────────────┐  
│       Browser         │  
└───────────┬───────────┘  
            │  
            ▼  
┌───────────────────────┐  
│   Reverse Proxy       │  
│   nginx / Caddy       │  
└───────────┬───────────┘  
            │  
            ▼  
┌───────────────────────┐  
│      Django           │  
│      Gunicorn         │  
└───────┬───────┬───────┘  
        │       │  
        │       └──────────────┐  
        ▼                      ▼  
┌───────────────┐      ┌────────────────┐  
│  PostgreSQL   │      │ Redis          │  
└───────────────┘      └───────┬────────┘  
                               │  
                         ┌─────▼─────┐  
                         │  Celery   │  
                         │  Worker   │  
                         └───────────┘  
                               │  
                         ┌─────▼─────┐  
                         │ Celery     │  
                         │ Beat       │  
                         └───────────┘

For development, this can be simplified while maintaining production parity.

---

# **61\. Scheduled jobs**

At minimum:

### **Morning snapshot generation**

Runs daily and generates required morning snapshots.

### **Leave permission expiration**

Runs daily and expires stale permissions.

Potential future jobs:

* report generation  
* cleanup  
* notifications  
* backups

All scheduled jobs should be:

* idempotent  
* observable  
* logged  
* retry-safe

---

# **62\. Testing requirements**

This project should have significantly more automated testing than the original PHP application.

At minimum:

## **Unit tests**

Test:

* presence transitions  
* morning cutoff calculation  
* weekend date calculation  
* permission expiration  
* room capacity  
* room assignment overlap  
* state transitions

## **Integration tests**

Test:

* student presence workflow  
* evening inspection  
* morning snapshot generation  
* weekend approval/check workflow  
* leave permission workflow  
* teacher access restrictions

## **Authorization tests**

Explicitly test:

Student cannot access Student B  
Teacher cannot access unrelated group  
Porter cannot modify presence  
Teacher cannot perform management-only operation  
Management cannot perform administrator-only destructive operation

## **Import tests**

Test:

* malformed CSV  
* duplicates  
* conflicts  
* overwrite decisions  
* transactional rollback

---

# **63\. Migration from the old system**

The old system should be treated as the **source of business requirements**, not the architectural template.

If migrating existing production data later:

Old WordPress DB  
       ↓  
Extraction  
       ↓  
Transformation  
       ↓  
Validation  
       ↓  
Django import  
       ↓  
Verification

Create a dedicated migration/import command.

Do not try to make the new Django ORM directly use the old WordPress tables.

---

# **64\. Legacy system facts**

The current system is WordPress with a custom plugin:

wp-content/plugins/kollegiumi-jelenlet/

and a separate PHP portal:

/portal/

The custom plugin contains:

people  
students  
rooms  
presence  
calendar  
evening-check  
morning-check  
room-checks  
weekend-stay  
leave-permissions  
exports  
frontend  
authentication  
inspection infrastructure

There is also a nested `portal2.zip` containing the more complete portal implementation.

The Git history shows the application was actively evolving and was already moving toward a separate portal architecture.

Therefore the Django rebuild should consolidate these fragmented layers into one coherent application.

---

# **65\. Existing navigation/functionality**

The main operational portal currently exposes concepts equivalent to:

Dashboard

Presence  
    Current presence  
    Presence log  
    Student presence details

Checks  
    Evening check  
    Morning check  
    Room checks

Leave permissions

Room check / own room

Weekend stay

Logs  
    Presence/history  
    Room order/history

Data  
    Rooms  
    Students  
    Teachers & groups  
    Modules

Teacher  
    Students  
    Change requests

Profile

Password change

Maintenance

Logout

The exact navigation can be redesigned.

---

# **66\. UX principle**

Do not copy the existing PHP UI literally.

Keep the workflows, but create a professional interface.

The application should feel like a modern internal operations platform.

For example:

### **Student dashboard**

Good evening, John

Current status  
┌─────────────────────────┐  
│ 🟢 Bent                 │  
└─────────────────────────┘

\[ BEJÖTTEM \]

\[ KIMENTEM \]

Current leave permission  
...

My room  
...

Weekend stay  
...

### **Staff dashboard**

Today's overview

Students inside      124  
Outside               17  
Not checked            3

Evening inspection  
Floor 1     ✓ Complete  
Floor 2     ● In progress  
Floor 3     ○ Not started

Morning inspection  
...

Room inspection  
...

Weekend stays  
...  
---

# **67\. API strategy**

Do not automatically build a REST API for everything.

First build the actual server-rendered application using Django.

Use:

* Django views  
* forms  
* templates  
* HTMX where useful

Introduce DRF only when there is a genuine API consumer.

This keeps the first implementation simpler and more maintainable.

---

# **68\. Recommended frontend approach**

For this particular application:

**Django templates \+ HTMX \+ modern CSS** is a very good default.

It provides:

* fast development  
* excellent Django integration  
* simple deployment  
* progressive enhancement  
* minimal JavaScript  
* good mobile UX

Use JavaScript only where it materially improves the interaction.

Examples:

* presence updates  
* inspection row updates  
* modal dialogs  
* live status refresh  
* import preview interactions

---

# **69\. Reporting**

Reports should be treated as first-class functionality.

Potential reports:

* current student presence  
* historical presence  
* evening inspection report  
* morning inspection report  
* room inspection history  
* weekend stay roster  
* weekend inspection sheet  
* student/room occupancy  
* leave permission history

Reports should be generated from query/service layers rather than directly assembling SQL inside templates.

---

# **70\. Important design principle: current state vs history**

This is one of the most important architectural requirements.

The application contains many concepts where there is both:

### **Current state**

and:

### **Historical record**

For example:

Student.current\_presence  
        \+  
PresenceEvent history

or:

Current leave permission  
        \+  
LeavePermissionHistory

or:

Current room assignment  
        \+  
historical RoomAssignments

or:

Current room condition  
        \+  
RoomCheckHistory

Keep these concepts separate.

---

# **71\. Important design principle: snapshots**

Morning checks demonstrate another important principle.

A historical inspection must not depend on mutable current data.

When creating a morning snapshot, copy the required historical values.

For example:

MorningSnapshotItem  
    student\_id  
    student\_name\_snapshot  
    room\_number\_snapshot  
    floor\_snapshot  
    class\_snapshot  
    evening\_result  
    calculated\_result  
    final\_result

This is intentional denormalization for historical integrity.

---

# **72\. Important design principle: transactions**

The following operations should generally be transactional:

* changing student presence  
* approving a weekend request  
* closing an inspection  
* reopening an inspection  
* generating a morning snapshot  
* granting/revoking leave permission  
* applying CSV imports  
* approving student change requests  
* room assignment changes

A failure halfway through should not leave the database in an inconsistent state.

---

# **73\. Important design principle: don't reproduce legacy bugs**

The existing application contains legacy code, duplicate files, old versions, and partially implemented routes.

Examples include:

\*\_old.php  
.git\_old  
older service implementations  
placeholder frontend routes

These should **not** automatically be treated as requirements.

Use the current implemented portal and business logic to determine intended behavior.

When two implementations conflict:

1. Prefer the newer implementation.  
2. Prefer actual user-facing behavior.  
3. Prefer the current service/domain logic.  
4. Preserve important business rules.  
5. Do not preserve implementation bugs.

---

# **74\. Definition of success**

The rebuild is successful when the new application allows:

### **Students**

* log in  
* manage password  
* view their profile  
* see their room  
* see their current leave permission  
* mark themselves inside  
* mark themselves outside with a reason  
* register for weekend stays  
* see relevant room/inspection information

### **Teachers**

* see authorized students  
* edit permitted student information  
* submit change requests  
* manage student presence  
* view presence history  
* perform inspections  
* manage appropriate weekend activities  
* manage leave permissions

### **Porters**

* view current presence

### **Management**

* manage operational data  
* manage inspections  
* manage students  
* manage rooms  
* manage teachers/groups  
* review requests  
* manage weekend stays  
* manage permissions  
* generate reports  
* import/export data

### **Administrators**

Everything above plus:

* users  
* permissions  
* settings  
* modules  
* maintenance  
* system-level operations

---

# **75\. Implementation order**

Do **not** attempt to build every module simultaneously.

Recommended order:

### **Phase 1 — Foundation**

Docker  
PostgreSQL  
Django  
settings  
authentication  
users  
roles  
permissions  
base UI  
audit framework

### **Phase 2 — Core data**

Students  
Rooms  
Groups  
Teachers  
School years  
Room assignments

### **Phase 3 — Presence**

Status types  
Current status  
Presence events  
Student self-service  
Staff presence view  
Presence history

### **Phase 4 — Inspections**

Calendar  
Evening checks  
Morning snapshots  
Room checks

### **Phase 5 — Weekend**

Weekend stays  
Guest students  
Weekend approvals  
Weekend checks  
Printable report

### **Phase 6 — Leave permissions**

Current permissions  
History  
Expiration job

### **Phase 7 — Administration**

Imports  
Exports  
Reports  
Module configuration  
Maintenance

### **Phase 8 — Hardening**

Tests  
Security  
Performance  
Audit review  
Backups  
Monitoring  
Production deployment  
---

# **76\. Coding AI instructions**

When implementing this project, follow these rules:

1. **Do not recreate WordPress.**  
2. **Do not reproduce the old PHP architecture.**  
3. **Do not copy the old database schema blindly.**  
4. Use Django ORM.  
5. Use PostgreSQL.  
6. Use migrations.  
7. Keep business logic out of templates.  
8. Keep complex business logic out of views.  
9. Use service/domain functions for workflows.  
10. Use transactions for multi-step state changes.  
11. Enforce important constraints at the database level.  
12. Implement object-level authorization.  
13. Preserve historical records.  
14. Use snapshots when historical data must remain immutable.  
15. Use background jobs for scheduled operations.  
16. Make scheduled operations idempotent.  
17. Write tests alongside functionality.  
18. Do not expose sensitive information in logs.  
19. Do not put secrets in source control.  
20. Prefer simple architecture over unnecessary infrastructure.  
21. Do not add an API/frontend framework unless there is a clear reason.  
22. Build the application incrementally.  
23. Before implementing a module, understand its domain model and state transitions.  
24. When legacy behavior is ambiguous, document the ambiguity rather than silently inventing behavior.  
25. Favor maintainability and correctness over matching the old implementation line-for-line.

---

## **The key mental model for the AI**

The application is **not primarily a "presence website."**

It is a **dormitory operations management system** whose central entities and relationships are:

                        ┌─────────────┐  
                         │   SCHOOL    │  
                         │    YEAR     │  
                         └──────┬──────┘  
                                │  
        ┌───────────────────────┼───────────────────────┐  
        │                       │                       │  
        ▼                       ▼                       ▼  
     GROUPS                 STUDENTS                 ROOMS  
        │                       │                       │  
        │                       │                       │  
     TEACHERS                   ├──── Room Assignments ─┘  
                                │  
                                ├──── Current Presence  
                                │          │  
                                │          └── Presence Events  
                                │  
                                ├──── Leave Permission  
                                │          └── Permission History  
                                │  
                                ├──── Weekend Stays  
                                │          └── Weekend Checks  
                                │  
                                └──── Inspections  
                                           │  
                              ┌────────────┼────────────┐  
                              ▼            ▼            ▼  
                          Evening       Morning       Room  
                           Check        Snapshot      Check

And the **most important architectural distinction** is:

CURRENT STATE  
     \+  
HISTORICAL EVENTS  
     \+  
HISTORICAL SNAPSHOTS

rather than putting everything into one mutable record.

---

### **One thing I would change from the previous summary**

I would **not** tell the coding AI that the old `/portal/` application is necessarily "the current intended application" and have it reproduce that code. The source contains enough evidence of ongoing refactoring and legacy/duplicate implementations that it is safer to treat the **implemented business behavior as the specification**, while treating the PHP implementation itself as legacy.

That gives you a much better starting point for a proper Django rewrite.

