from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.capabilities import Role
from apps.audit.services import AuditAction, record_audit

from .models import StudentProfile

User = get_user_model()


class StudentForm(forms.ModelForm):
    """Edit form whose visible fields depend on the actor's rights.

    Fields the actor may not write are removed entirely rather than merely
    disabled, so a crafted POST cannot slip past.
    """

    class Meta:
        model = StudentProfile
        fields = [
            "full_name",
            "education_id",
            "birth_date",
            "school_name",
            "school_class",
            "group",
            "phone",
            "address",
            "guardian_name",
            "guardian_phone",
            "guardian_email",
            "emergency_contact_name",
            "emergency_contact_phone",
            "medical_notes",
            "staff_notes",
            "move_in_date",
            "move_out_date",
        ]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "move_in_date": forms.DateInput(attrs={"type": "date"}),
            "move_out_date": forms.DateInput(attrs={"type": "date"}),
            "medical_notes": forms.Textarea(attrs={"rows": 3}),
            "staff_notes": forms.Textarea(attrs={"rows": 3}),
            "address": forms.TextInput(),
        }

    def __init__(self, *args, allowed_fields=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_fields is not None:
            for name in list(self.fields):
                if name not in allowed_fields:
                    del self.fields[name]

    def clean_education_id(self):
        value = (self.cleaned_data.get("education_id") or "").strip()
        return value or None


class StudentCreateForm(StudentForm):
    """Creates the login identity and the profile together."""

    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    temporary_password = forms.CharField(
        min_length=10,
        widget=forms.PasswordInput,
        help_text="A diák az első belépéskor köteles lesz megváltoztatni.",
    )

    field_order = ["username", "email", "temporary_password", "full_name"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ez a felhasználónév már foglalt.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Ez az e-mail cím már használatban van.")
        return email

    @transaction.atomic
    def save(self, actor=None, commit=True):
        user = User.objects.create_user(
            username=self.cleaned_data["username"],
            email=self.cleaned_data["email"],
            password=self.cleaned_data["temporary_password"],
            role=Role.STUDENT,
            must_change_password=True,
        )
        student = super().save(commit=False)
        student.user = user
        student.save()
        record_audit(user=actor, action=AuditAction.CREATE, target=student)
        return student


class StudentChangeRequestForm(forms.Form):
    """Free-form proposal from a teacher for fields they cannot write."""

    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    PROPOSABLE_FIELDS = [
        "full_name",
        "school_name",
        "school_class",
        "phone",
        "address",
        "guardian_name",
        "guardian_phone",
        "guardian_email",
        "emergency_contact_name",
        "emergency_contact_phone",
        "medical_notes",
    ]

    def __init__(self, *args, student=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.student = student
        for name in self.PROPOSABLE_FIELDS:
            field = StudentProfile._meta.get_field(name)
            self.fields[name] = forms.CharField(
                required=False,
                label=field.verbose_name,
                initial=getattr(student, name, "") if student else "",
                widget=forms.Textarea(attrs={"rows": 2})
                if field.get_internal_type() == "TextField"
                else forms.TextInput(),
            )

    def proposed_changes(self):
        """Only genuinely changed fields become part of the request."""
        changes = {}
        for name in self.PROPOSABLE_FIELDS:
            new = (self.cleaned_data.get(name) or "").strip()
            old = (getattr(self.student, name, "") or "") if self.student else ""
            if new != str(old):
                changes[name] = new
        return changes
