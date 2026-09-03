"""Validated CSV import/export for rooms.

The pipeline is deliberately two-phase: parse + validate into a preview, then
apply the preview inside one transaction once the operator has resolved
conflicts. Raw rows never reach the database (spec section 44).
"""

import csv
import io
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.capabilities import Capability
from apps.audit.services import AuditAction, record_audit

from .models import Room, infer_floor

COLUMNS = ["number", "floor", "capacity", "is_active", "notes"]
MAX_ROWS = 5000
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

TRUTHY = {"1", "true", "yes", "y", "igen", "aktiv", "aktív"}
FALSY = {"0", "false", "no", "n", "nem", "inaktiv", "inaktív"}


@dataclass
class RowResult:
    line_number: int
    raw: dict
    data: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    action: str = "create"  # create | conflict | unchanged
    existing_id: int | None = None
    differences: dict = field(default_factory=dict)

    @property
    def is_valid(self):
        return not self.errors


@dataclass
class ImportPreview:
    rows: list = field(default_factory=list)

    @property
    def valid_rows(self):
        return [r for r in self.rows if r.is_valid]

    @property
    def error_rows(self):
        return [r for r in self.rows if not r.is_valid]

    @property
    def conflicts(self):
        return [r for r in self.rows if r.is_valid and r.action == "conflict"]

    @property
    def creates(self):
        return [r for r in self.rows if r.is_valid and r.action == "create"]

    @property
    def unchanged(self):
        return [r for r in self.rows if r.is_valid and r.action == "unchanged"]

    def to_session(self):
        return [
            {
                "line_number": r.line_number,
                "data": r.data,
                "action": r.action,
                "existing_id": r.existing_id,
            }
            for r in self.rows
            if r.is_valid
        ]


def template_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    writer.writerow(["101", "1", "4", "true", "Példa szoba"])
    writer.writerow(["102", "1", "2", "true", ""])
    return buffer.getvalue()


def export_csv(queryset=None):
    queryset = queryset if queryset is not None else Room.objects.all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    for room in queryset.order_by("floor", "number"):
        writer.writerow(
            [room.number, room.floor, room.capacity, "true" if room.is_active else "false", room.notes]
        )
    return buffer.getvalue()


def _parse_bool(value, default=True):
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in TRUTHY:
        return True
    if normalized in FALSY:
        return False
    raise ValueError(f"a(z) „{value}” nem értelmezhető igen/nem értékként")


def _decode(upload):
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError("A feltöltött fájl túl nagy (legfeljebb 2 MB).")
    raw = upload.read()
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin-2"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError("A fájl nem olvasható UTF-8 vagy CP1250 szövegként.")


def build_preview(upload):
    """Parse and validate an uploaded CSV into an :class:`ImportPreview`."""
    text = _decode(upload)
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    if not reader.fieldnames:
        raise ValidationError("A CSV fájl üres.")
    normalized_headers = {(h or "").strip().lower() for h in reader.fieldnames}
    if "number" not in normalized_headers:
        raise ValidationError(
            "Hiányzik a kötelező „number” oszlop. Várt oszlopok: " + ", ".join(COLUMNS)
        )

    existing = {room.number: room for room in Room.objects.all()}
    seen_numbers = {}
    preview = ImportPreview()

    for index, raw_row in enumerate(reader, start=2):
        if index - 1 > MAX_ROWS:
            raise ValidationError(f"A fájl több mint {MAX_ROWS} sort tartalmaz.")
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
        if not any(row.values()):
            continue

        result = RowResult(line_number=index, raw=row)
        number = row.get("number", "")

        if not number:
            result.errors.append("A „number” oszlop kitöltése kötelező.")
        elif len(number) > 16:
            result.errors.append("A „number” hosszabb 16 karakternél.")
        elif number in seen_numbers:
            result.errors.append(f"Ismétlődés: a(z) {seen_numbers[number]}. sorral azonos.")
        else:
            seen_numbers[number] = index

        floor_raw = row.get("floor", "")
        floor = None
        if floor_raw:
            try:
                floor = int(floor_raw)
            except ValueError:
                result.errors.append(f"A „floor” csak egész szám lehet (kapott érték: „{floor_raw}”).")
        elif number:
            floor = infer_floor(number)
            if floor is None:
                result.errors.append("A „floor” hiányzik, és a szobaszámból sem vezethető le.")
        if floor is not None and floor < 0:
            result.errors.append("A „floor” nem lehet negatív.")

        capacity = None
        capacity_raw = row.get("capacity", "")
        if not capacity_raw:
            result.errors.append("A „capacity” oszlop kitöltése kötelező.")
        else:
            try:
                capacity = int(capacity_raw)
            except ValueError:
                result.errors.append(f"A „capacity” csak egész szám lehet (kapott érték: „{capacity_raw}”).")
            else:
                if capacity < 1 or capacity > 20:
                    result.errors.append("A „capacity” értéke 1 és 20 között lehet.")

        try:
            is_active = _parse_bool(row.get("is_active"), default=True)
        except ValueError as exc:
            result.errors.append(f"„is_active”: {exc}")
            is_active = True

        notes = row.get("notes", "")[:1000]

        if result.is_valid:
            result.data = {
                "number": number,
                "floor": floor,
                "capacity": capacity,
                "is_active": is_active,
                "notes": notes,
            }
            current = existing.get(number)
            if current is None:
                result.action = "create"
            else:
                result.existing_id = current.pk
                differences = {
                    key: (getattr(current, key), value)
                    for key, value in result.data.items()
                    if key != "number" and getattr(current, key) != value
                }
                result.differences = differences
                result.action = "conflict" if differences else "unchanged"

        preview.rows.append(result)

    if not preview.rows:
        raise ValidationError("A CSV fájl nem tartalmazott adatsort.")
    return preview


@transaction.atomic
def apply_import(*, rows, actor, overwrite_ids=()):
    """Apply a previously built preview.

    ``rows`` is the session-serialized preview; ``overwrite_ids`` names the
    conflicting rooms the operator chose to overwrite. Everything happens in
    one transaction, so a late failure rolls the whole import back.
    """
    if not actor.has_capability(Capability.IMPORT_DATA):
        raise PermissionDenied("Nincs jogosultságod adatot importálni.")

    overwrite_ids = {int(i) for i in overwrite_ids}
    created = updated = skipped = 0

    for row in rows:
        data = row["data"]
        if row["action"] == "create":
            Room.objects.create(**data)
            created += 1
        elif row["action"] == "conflict" and row["existing_id"] in overwrite_ids:
            room = Room.objects.select_for_update().get(pk=row["existing_id"])
            if data["capacity"] < room.occupancy:
                raise ValidationError(
                    f"{room.number} szoba: a megadott férőhely ({data['capacity']}) kevesebb "
                    f"a jelenlegi létszámnál ({room.occupancy}). Az import megszakadt."
                )
            for key, value in data.items():
                setattr(room, key, value)
            room.full_clean()
            room.save()
            updated += 1
        else:
            skipped += 1

    summary = {"created": created, "updated": updated, "skipped": skipped}
    record_audit(
        user=actor,
        action=AuditAction.IMPORT,
        target_type="rooms.Room",
        target_repr="Room CSV import",
        new_value=summary,
    )
    return summary
