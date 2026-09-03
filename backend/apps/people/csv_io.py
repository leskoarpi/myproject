"""Validated CSV import/export for teachers (spec section 45).

Same two-phase shape as the room importer: validate into a preview, apply in
one transaction. Accounts are created with a temporary password and a forced
password change; passwords are never read from the CSV.
"""

import csv
import io
import secrets
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction

from apps.accounts.capabilities import Capability, Role
from apps.audit.services import AuditAction, record_audit

from .models import Group, Teacher

User = get_user_model()

COLUMNS = ["username", "email", "full_name", "phone", "primary_group", "groups"]
MAX_ROWS = 2000
MAX_UPLOAD_BYTES = 1024 * 1024


@dataclass
class TeacherRow:
    line_number: int
    data: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    action: str = "create"  # create | conflict | unchanged
    existing_user_id: int | None = None
    missing_groups: list = field(default_factory=list)

    @property
    def is_valid(self):
        return not self.errors


@dataclass
class TeacherImportPreview:
    rows: list = field(default_factory=list)

    @property
    def creates(self):
        return [r for r in self.rows if r.is_valid and r.action == "create"]

    @property
    def conflicts(self):
        return [r for r in self.rows if r.is_valid and r.action == "conflict"]

    @property
    def error_rows(self):
        return [r for r in self.rows if not r.is_valid]

    def to_session(self):
        return [
            {
                "line_number": r.line_number,
                "data": r.data,
                "action": r.action,
                "existing_user_id": r.existing_user_id,
            }
            for r in self.rows
            if r.is_valid
        ]


def template_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    writer.writerow(["knagy", "nagy.k@example.org", "Nagy Katalin", "+36301234567", "A1", "A1;A2"])
    return buffer.getvalue()


def export_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    for teacher in Teacher.objects.select_related("user", "primary_group").prefetch_related(
        "groups"
    ):
        writer.writerow(
            [
                teacher.user.username,
                teacher.user.email,
                teacher.full_name,
                teacher.phone,
                teacher.primary_group.code if teacher.primary_group else "",
                ";".join(g.code for g in teacher.groups.all()),
            ]
        )
    return buffer.getvalue()


def _decode(upload):
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError("The uploaded file is too large (limit: 1 MB).")
    raw = upload.read()
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin-2"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError("The file could not be decoded as UTF-8 or CP1250 text.")


def build_preview(upload):
    text = _decode(upload)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValidationError("The CSV file is empty.")

    headers = {(h or "").strip().lower() for h in reader.fieldnames}
    for required in ("username", "email", "full_name"):
        if required not in headers:
            raise ValidationError(f"Missing required column '{required}'.")

    known_groups = {g.code.lower(): g for g in Group.objects.all()}
    existing_users = {u.username.lower(): u for u in User.objects.all()}
    existing_emails = {u.email.lower(): u for u in User.objects.exclude(email="")}
    seen = {}
    preview = TeacherImportPreview()

    for index, raw_row in enumerate(reader, start=2):
        if index - 1 > MAX_ROWS:
            raise ValidationError(f"The file has more than {MAX_ROWS} rows.")
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
        if not any(row.values()):
            continue

        result = TeacherRow(line_number=index)
        username = row.get("username", "")
        email = row.get("email", "").lower()
        full_name = row.get("full_name", "")

        if not username:
            result.errors.append("'username' is required.")
        elif username.lower() in seen:
            result.errors.append(f"Duplicate of line {seen[username.lower()]} in this file.")
        else:
            seen[username.lower()] = index

        if not full_name:
            result.errors.append("'full_name' is required.")

        if not email:
            result.errors.append("'email' is required.")
        else:
            try:
                validate_email(email)
            except ValidationError:
                result.errors.append(f"'{email}' is not a valid email address.")

        group_codes = [c.strip() for c in (row.get("groups", "") or "").split(";") if c.strip()]
        primary = row.get("primary_group", "").strip()
        if primary and primary not in group_codes:
            group_codes.insert(0, primary)
        for code in group_codes:
            if code.lower() not in known_groups:
                result.missing_groups.append(code)
        if result.missing_groups:
            result.errors.append(
                "Unknown group(s): " + ", ".join(sorted(set(result.missing_groups)))
            )

        if result.is_valid:
            existing = existing_users.get(username.lower())
            email_owner = existing_emails.get(email)
            if email_owner is not None and (existing is None or email_owner.pk != existing.pk):
                result.errors.append(
                    f"The email {email} already belongs to '{email_owner.username}'."
                )

        if result.is_valid:
            result.data = {
                "username": username,
                "email": email,
                "full_name": full_name,
                "phone": row.get("phone", ""),
                "primary_group": primary,
                "groups": group_codes,
            }
            existing = existing_users.get(username.lower())
            if existing is None:
                result.action = "create"
            else:
                result.existing_user_id = existing.pk
                result.action = "conflict"

        preview.rows.append(result)

    if not preview.rows:
        raise ValidationError("The CSV file contained no data rows.")
    return preview


@transaction.atomic
def apply_import(*, rows, actor, overwrite_user_ids=()):
    if not actor.has_capability(Capability.IMPORT_DATA):
        raise PermissionDenied("Missing capability to import data.")
    if not actor.has_capability(Capability.MANAGE_TEACHERS):
        raise PermissionDenied("Missing capability to manage teachers.")

    overwrite_user_ids = {int(i) for i in overwrite_user_ids}
    groups = {g.code.lower(): g for g in Group.objects.all()}
    created = updated = skipped = 0
    temporary_passwords = {}

    for row in rows:
        data = row["data"]
        group_objs = [groups[c.lower()] for c in data["groups"] if c.lower() in groups]
        primary = groups.get(data["primary_group"].lower()) if data["primary_group"] else None

        if row["action"] == "create":
            password = secrets.token_urlsafe(12)
            user = User.objects.create_user(
                username=data["username"],
                email=data["email"],
                password=password,
                role=Role.TEACHER,
                must_change_password=True,
            )
            teacher = Teacher.objects.create(
                user=user,
                full_name=data["full_name"],
                phone=data["phone"],
                primary_group=primary,
            )
            teacher.groups.set(group_objs)
            temporary_passwords[data["username"]] = password
            created += 1
        elif row["action"] == "conflict" and row["existing_user_id"] in overwrite_user_ids:
            user = User.objects.select_for_update().get(pk=row["existing_user_id"])
            user.email = data["email"]
            user.save(update_fields=["email"])
            teacher, _ = Teacher.objects.get_or_create(
                user=user, defaults={"full_name": data["full_name"]}
            )
            teacher.full_name = data["full_name"]
            teacher.phone = data["phone"]
            teacher.primary_group = primary
            teacher.save()
            teacher.groups.set(group_objs)
            updated += 1
        else:
            skipped += 1

    summary = {"created": created, "updated": updated, "skipped": skipped}
    record_audit(
        user=actor,
        action=AuditAction.IMPORT,
        target_type="people.Teacher",
        target_repr="Teacher CSV import",
        new_value=summary,
    )
    # Temporary passwords are returned to the caller for one-time display and
    # deliberately never written to the audit log.
    return summary, temporary_passwords
