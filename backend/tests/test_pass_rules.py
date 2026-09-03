"""Pass rules: an attribute of the student, readable dormitory-wide.

The dormitory runs its own paper pass system, so this app no longer issues or
expires anything - it records who may be given a pass and by whom.
"""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.capabilities import Role
from apps.leave_permissions.models import PassEligibility, PassRuleHistory, StudentPassRule
from apps.leave_permissions.services import (
    can_set_pass_rule,
    pass_rule_queryset_for_user,
    rule_for,
    set_pass_rule,
)

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    f.seed_status_types()
    f.seed_modules()
    f.make_school_year()
    group_a, group_b = f.make_group("A1"), f.make_group("B1")
    return {
        "group_a": group_a,
        "group_b": group_b,
        "teacher_a": f.make_teacher(group=group_a),
        "teacher_b": f.make_teacher(group=group_b),
        "student_a": f.make_student(group=group_a, name="A Diák"),
        "student_b": f.make_student(group=group_b, name="B Diák"),
        "management": f.make_user(Role.MANAGEMENT),
        "porter": f.make_user(Role.PORTER),
    }


def test_default_rule_is_allowed(world):
    rule = rule_for(world["student_a"])
    assert rule.eligibility == PassEligibility.ALLOWED
    assert rule.is_restricted is False


def test_setting_a_rule_records_history(world):
    rule = set_pass_rule(
        student=world["student_a"],
        actor=world["teacher_a"].user,
        eligibility=PassEligibility.TEACHER_ONLY,
    )

    assert rule.eligibility == PassEligibility.TEACHER_ONLY
    assert rule.is_restricted is True
    assert rule.set_by == world["teacher_a"].user

    entry = PassRuleHistory.objects.get(student=world["student_a"])
    assert entry.previous_eligibility == PassEligibility.ALLOWED
    assert entry.new_eligibility == PassEligibility.TEACHER_ONLY


def test_repeated_identical_set_is_not_recorded(world):
    set_pass_rule(
        student=world["student_a"],
        actor=world["teacher_a"].user,
        eligibility=PassEligibility.DENIED,
    )
    set_pass_rule(
        student=world["student_a"],
        actor=world["teacher_a"].user,
        eligibility=PassEligibility.DENIED,
    )
    assert PassRuleHistory.objects.filter(student=world["student_a"]).count() == 1


def test_other_requires_a_note(world):
    with pytest.raises(ValidationError):
        set_pass_rule(
            student=world["student_a"],
            actor=world["teacher_a"].user,
            eligibility=PassEligibility.OTHER,
        )

    rule = set_pass_rule(
        student=world["student_a"],
        actor=world["teacher_a"].user,
        eligibility=PassEligibility.OTHER,
        note="Csak szülői egyeztetés után.",
    )
    assert rule.note.startswith("Csak szülői")


def test_database_rejects_other_without_a_note(world):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            StudentPassRule.objects.create(
                student=world["student_a"], eligibility=PassEligibility.OTHER, note=""
            )


def test_unknown_eligibility_is_rejected(world):
    with pytest.raises(ValidationError):
        set_pass_rule(
            student=world["student_a"], actor=world["management"], eligibility="whatever"
        )


# --------------------------------------------------------------------------
# Scope: read wide, write narrow
# --------------------------------------------------------------------------


def test_every_teacher_reads_the_whole_dormitory(world):
    rule_for(world["student_a"])
    rule_for(world["student_b"])

    visible = pass_rule_queryset_for_user(world["teacher_a"].user)
    students = {r.student_id for r in visible}

    assert world["student_a"].pk in students
    assert world["student_b"].pk in students  # not in this teacher's group


def test_porter_can_read_pass_rules(world):
    rule_for(world["student_a"])
    assert pass_rule_queryset_for_user(world["porter"]).exists()


def test_a_student_reads_only_their_own(world):
    rule_for(world["student_a"])
    rule_for(world["student_b"])

    visible = pass_rule_queryset_for_user(world["student_a"].user)
    assert [r.student_id for r in visible] == [world["student_a"].pk]


def test_writing_stays_with_the_students_own_teacher(world):
    assert can_set_pass_rule(world["teacher_a"].user, world["student_a"])
    assert not can_set_pass_rule(world["teacher_a"].user, world["student_b"])

    with pytest.raises(PermissionDenied):
        set_pass_rule(
            student=world["student_b"],
            actor=world["teacher_a"].user,
            eligibility=PassEligibility.DENIED,
        )


def test_management_may_set_any_students_rule(world):
    rule = set_pass_rule(
        student=world["student_b"],
        actor=world["management"],
        eligibility=PassEligibility.DENIED,
    )
    assert rule.eligibility == PassEligibility.DENIED


def test_porter_cannot_set_a_rule(world):
    with pytest.raises(PermissionDenied):
        set_pass_rule(
            student=world["student_a"],
            actor=world["porter"],
            eligibility=PassEligibility.DENIED,
        )


def test_student_cannot_set_their_own_rule(world):
    with pytest.raises(PermissionDenied):
        set_pass_rule(
            student=world["student_a"],
            actor=world["student_a"].user,
            eligibility=PassEligibility.ALLOWED,
        )


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


def test_list_shows_students_from_other_groups(client, world):
    client.force_login(world["teacher_a"].user)
    body = client.get(reverse("passes:index")).content.decode()

    assert "A Diák" in body
    assert "B Diák" in body


def test_setting_out_of_group_over_http_is_refused(client, world):
    client.force_login(world["teacher_a"].user)
    response = client.post(
        reverse("passes:set_rule", kwargs={"student_id": world["student_b"].pk}),
        {"eligibility": PassEligibility.DENIED},
    )

    assert response.status_code == 404  # outside the writable scope
    assert not StudentPassRule.objects.filter(
        student=world["student_b"], eligibility=PassEligibility.DENIED
    ).exists()
