from django import forms
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Client, LinkedInReachout, TeamMember

_TEXT_INPUT = {"class": "form-control"}
_SELECT = {"class": "form-select"}
_TEXTAREA = {"class": "form-control", "rows": 3}


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


class ClientStatusForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["status"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
        }


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
            render_value=True,
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
            render_value=True,
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
                "placeholder": "Mailbox app password",
            },
            render_value=True,
        ),
    )
