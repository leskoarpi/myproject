"""Populate a development database with a small, realistic dataset.

Development/testing only - it refuses to run when DEBUG is off unless
--force is passed, so it cannot be fired at production by accident.
"""

import datetime as dt
import random

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Role
from apps.core.models import SchoolYear
from apps.leave_permissions.services import grant_leave_permission
from apps.people.models import Group, Teacher
from apps.presence.models import StatusType
from apps.presence.services import change_student_presence, ensure_presence_row
from apps.rooms.models import Room
from apps.rooms.services import assign_student_to_room
from apps.students.models import StudentProfile

User = get_user_model()

FIRST_NAMES = ["Anna", "Bence", "Csenge", "Dávid", "Eszter", "Ferenc", "Gréta", "Hanna",
               "István", "Júlia", "Kata", "Levente", "Márk", "Nóra", "Olivér", "Petra",
               "Réka", "Soma", "Tamás", "Vera", "Zsófia", "Ákos", "Balázs", "Dorka"]
LAST_NAMES = ["Nagy", "Kovács", "Tóth", "Szabó", "Horváth", "Varga", "Kiss", "Molnár",
              "Németh", "Farkas", "Balogh", "Papp", "Lakatos", "Juhász", "Mészáros"]


class Command(BaseCommand):
    help = "Create demo users, rooms and students for local development."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=40)
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG=False.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to create demo data with DEBUG=False. Pass --force if you "
                "really mean it."
            )

        year = SchoolYear.current()
        if year is None:
            raise CommandError("No active school year. Run seed_reference_data --school-year first.")
        if not StatusType.objects.exists():
            raise CommandError("No status types. Run seed_reference_data first.")

        rng = random.Random(20250901)
        password = "demo-password-2025"

        admin = self._user("admin", "admin@example.invalid", Role.ADMIN, password, staff=True)
        management = self._user("vezeto", "vezeto@example.invalid", Role.MANAGEMENT, password)
        porter = self._user("portas", "portas@example.invalid", Role.PORTER, password)

        groups = []
        for code, name in [("A1", "A csoport"), ("B1", "B csoport"), ("C1", "C csoport")]:
            group, _ = Group.objects.get_or_create(code=code, defaults={"name": name})
            groups.append(group)

        teachers = []
        for index, group in enumerate(groups, start=1):
            user = self._user(f"tanar{index}", f"tanar{index}@example.invalid", Role.TEACHER, password)
            teacher, _ = Teacher.objects.get_or_create(
                user=user,
                defaults={"full_name": f"{LAST_NAMES[index]} {FIRST_NAMES[index]}", "primary_group": group},
            )
            teacher.groups.add(group)
            teachers.append(teacher)

        rooms = []
        for floor in (1, 2, 3):
            for index in range(1, 9):
                number = f"{floor}{index:02d}"
                room, _ = Room.objects.get_or_create(
                    number=number, defaults={"floor": floor, "capacity": rng.choice([2, 4, 4, 6])}
                )
                rooms.append(room)

        statuses = list(StatusType.objects.active())
        inside = StatusType.objects.filter(counts_as_inside=True).first()

        created = 0
        for index in range(options["students"]):
            username = f"diak{index + 1:03d}"
            if User.objects.filter(username=username).exists():
                continue
            user = self._user(username, f"{username}@example.invalid", Role.STUDENT, password)
            name = f"{rng.choice(LAST_NAMES)} {rng.choice(FIRST_NAMES)}"
            student = StudentProfile.objects.create(
                user=user,
                full_name=name,
                education_id=f"7{index + 100000:010d}",
                birth_date=dt.date(2007, 1, 1) + dt.timedelta(days=rng.randint(0, 1200)),
                school_name="Deák Ferenc Gimnázium",
                school_class=rng.choice(["9.A", "10.B", "11.C", "12.A"]),
                group=rng.choice(groups),
                phone=f"+3630{rng.randint(1000000, 9999999)}",
                guardian_name=f"{rng.choice(LAST_NAMES)} {rng.choice(FIRST_NAMES)}",
                guardian_phone=f"+3620{rng.randint(1000000, 9999999)}",
                move_in_date=year.start_date,
            )
            ensure_presence_row(student)

            room = next((r for r in rooms if r.free_places > 0), None)
            if room:
                assign_student_to_room(student=student, room=room, actor=admin, school_year=year,
                                       start_date=year.start_date)

            status = inside if rng.random() < 0.7 else rng.choice(statuses)
            change_student_presence(
                student=student,
                new_status=status,
                actor=admin,
                reason="demo adat",
                enforce_permissions=False,
                when=timezone.now() - dt.timedelta(hours=rng.randint(1, 20)),
            )
            if rng.random() < 0.25:
                grant_leave_permission(
                    student=student,
                    actor=management,
                    value="Hétköznap 20:00-ig",
                    expires_at=timezone.now() + dt.timedelta(days=rng.randint(1, 60)),
                )
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data ready: {created} student(s), {len(rooms)} room(s), "
                f"{len(teachers)} teacher(s).\n"
                f"Accounts: admin / vezeto / portas / tanar1-3 / diak001…  password: {password}"
            )
        )
        self.stdout.write(f"Porter account: {porter.username}")

    def _user(self, username, email, role, password, staff=False):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "role": role, "is_staff": staff, "is_superuser": staff},
        )
        if created:
            user.set_password(password)
            user.must_change_password = False
            user.save()
        return user
