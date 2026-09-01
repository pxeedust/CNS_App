import re

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from .models import Client, LinkedInReachout, OutreachCampaign, TeamMember

_TEXT_INPUT = {"class": "form-control"}
_SELECT = {"class": "form-select"}
_TEXTAREA = {"class": "form-control", "rows": 3}
MAX_CONTACT_IMPORT_BYTES = 5 * 1024 * 1024


class ClientForm(forms.ModelForm):
    """Full create / edit form for a Client record."""

    class Meta:
        model = Client
        fields = [
            "company_name",
            "contact_person",
            "email",
            "title",
            "industry",
            "phone",
            "city",
            "state",
            "country",
            "website",
            "linkedin_url",
            "keywords",
            "status",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs=_TEXT_INPUT),
            "contact_person": forms.TextInput(attrs=_TEXT_INPUT),
            "email": forms.EmailInput(attrs=_TEXT_INPUT),
            "title": forms.TextInput(attrs=_TEXT_INPUT),
            "industry": forms.TextInput(attrs=_TEXT_INPUT),
            "phone": forms.TextInput(attrs=_TEXT_INPUT),
            "city": forms.TextInput(attrs=_TEXT_INPUT),
            "state": forms.TextInput(attrs=_TEXT_INPUT),
            "country": forms.TextInput(attrs=_TEXT_INPUT),
            "website": forms.URLInput(attrs=_TEXT_INPUT),
            "linkedin_url": forms.URLInput(attrs=_TEXT_INPUT),
            "keywords": forms.Textarea(attrs=_TEXTAREA),
            "status": forms.Select(attrs=_SELECT),
        }
        labels = {
            "title": "Job Title",
            "linkedin_url": "LinkedIn URL",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            choice for choice in Client.Status.choices
            if choice[0] != Client.Status.SENDING
        ]


class ClientStatusForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["status"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            choice for choice in Client.Status.choices
            if choice[0] != Client.Status.SENDING
        ]


class ContactImportForm(forms.Form):
    csv_file = forms.FileField(
        label="Apollo CSV file",
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ".csv"}
        ),
    )
    skip_unverified = forms.BooleanField(
        required=False,
        initial=True,
        label='Skip rows where Email Status is not "Verified"',
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_csv_file(self):
        uploaded = self.cleaned_data["csv_file"]
        if not uploaded.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a CSV file exported from Apollo.")
        if uploaded.size > MAX_CONTACT_IMPORT_BYTES:
            max_mb = MAX_CONTACT_IMPORT_BYTES // (1024 * 1024)
            raise forms.ValidationError(f"CSV files must be {max_mb} MB or smaller.")
        return uploaded


class CampaignLaunchForm(forms.Form):
    """Validate the campaign selection and explicit bulk-send confirmation."""

    campaign_id = forms.ModelChoiceField(
        queryset=OutreachCampaign.objects.none(),
        required=False,
        empty_label="— Use built-in default template —",
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Email template",
    )
    confirm_send = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        error_messages={
            "required": "Confirm the recipient count before launching the campaign."
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["campaign_id"].queryset = OutreachCampaign.objects.all()


class OutreachCampaignForm(forms.ModelForm):
    _placeholder_re = re.compile(
        r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
        r"|(?<!\{)\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}(?!\})"
    )
    _allowed_placeholders = {
        "first_name", "last_name", "full_name", "contact_person", "title",
        "salutation", "company", "company_name", "industry", "keywords",
        "website", "linkedin_url", "city", "state", "country", "location",
        "sender_name", "sender_role", "sender_email", "email_body",
    }

    class Meta:
        model = OutreachCampaign
        fields = ["name", "target_industry", "subject_template", "email_template"]
        widgets = {
            "name": forms.TextInput(attrs=_TEXT_INPUT),
            "target_industry": forms.TextInput(
                attrs={**_TEXT_INPUT, "placeholder": "e.g. SaaS, Consulting, or All"}
            ),
            "subject_template": forms.TextInput(attrs=_TEXT_INPUT),
            "email_template": forms.Textarea(
                attrs={**_TEXTAREA, "rows": 14, "spellcheck": "true"}
            ),
        }

    def _clean_template(self, field_name):
        value = self.cleaned_data[field_name]
        placeholders = {
            (match.group(1) or match.group(2)).lower()
            for match in self._placeholder_re.finditer(value)
        }
        unknown = placeholders - self._allowed_placeholders
        if unknown:
            raise forms.ValidationError(
                f"Unknown placeholder(s): {', '.join(sorted(unknown))}."
            )
        return value

    def clean_subject_template(self):
        return self._clean_template("subject_template")

    def clean_email_template(self):
        return self._clean_template("email_template")


class LinkedInReachoutForm(forms.ModelForm):
    happened_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={**_TEXT_INPUT, "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )

    class Meta:
        model = LinkedInReachout
        fields = ["reachout_type", "happened_at", "notes"]
        widgets = {
            "reachout_type": forms.Select(attrs=_SELECT),
            "notes": forms.Textarea(
                attrs={
                    **_TEXTAREA,
                    "rows": 4,
                    "placeholder": "Optional context, message summary, or outcome.",
                }
            ),
        }
        labels = {
            "reachout_type": "LinkedIn Activity",
            "happened_at": "When it happened",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        happened_at = self.instance.happened_at if self.instance.pk else timezone.now()
        self.initial.setdefault(
            "happened_at",
            timezone.localtime(happened_at).strftime("%Y-%m-%dT%H:%M"),
        )


class CreateUserForm(forms.Form):
    """Form for admins to create a new team member account."""

    first_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs=_TEXT_INPUT)
    )
    last_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs=_TEXT_INPUT)
    )
    username = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs=_TEXT_INPUT)
    )
    email = forms.EmailField(widget=forms.EmailInput(attrs=_TEXT_INPUT))
    password = forms.CharField(
        min_length=8,
        widget=forms.PasswordInput(
            attrs={**_TEXT_INPUT, "placeholder": "Min 8 characters"}
        ),
    )
    sender_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={**_TEXT_INPUT, "placeholder": "Defaults to full name"}
        ),
    )
    sender_role = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={**_TEXT_INPUT, "placeholder": "e.g. Executive Head"}
        ),
    )
    mailbox_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(
            attrs={**_TEXT_INPUT, "placeholder": "Mailbox used for sends and scans"}
        ),
    )
    mailbox_app_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={
                **_TEXT_INPUT,
                "placeholder": "Mailbox app password",
            },
            render_value=False,
        ),
    )
    role = forms.ChoiceField(
        choices=TeamMember.Role.choices,
        initial=TeamMember.Role.MEMBER,
        widget=forms.Select(attrs=_SELECT),
    )

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password


class EditUserForm(forms.Form):
    """Form for admins to edit an existing team member."""

    first_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs=_TEXT_INPUT)
    )
    last_name = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs=_TEXT_INPUT)
    )
    email = forms.EmailField(widget=forms.EmailInput(attrs=_TEXT_INPUT))
    sender_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={**_TEXT_INPUT, "placeholder": "Defaults to full name"}
        ),
    )
    sender_role = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={**_TEXT_INPUT, "placeholder": "e.g. Executive Head"}
        ),
    )
    mailbox_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(
            attrs={**_TEXT_INPUT, "placeholder": "Mailbox used for sends and scans"}
        ),
    )
    mailbox_app_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={
                **_TEXT_INPUT,
                "placeholder": "Leave unchanged if not updating",
            },
            render_value=False,
        ),
    )
    role = forms.ChoiceField(
        choices=TeamMember.Role.choices,
        widget=forms.Select(attrs=_SELECT),
    )
    is_active = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Account active",
    )


class MailboxSettingsForm(forms.Form):
    sender_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={**_TEXT_INPUT, "placeholder": "Defaults to your full name"}
        ),
    )
    sender_role = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={**_TEXT_INPUT, "placeholder": "e.g. Executive Head"}
        ),
    )
    mailbox_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(
            attrs={**_TEXT_INPUT, "placeholder": "Mailbox used for send/scan"}
        ),
    )
    mailbox_app_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={
                **_TEXT_INPUT,
                "placeholder": "Leave blank to keep the current password",
            },
            render_value=False,
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(email__iexact=email)
        if self.user:
            existing = existing.exclude(pk=self.user.pk)
        if existing.exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email
