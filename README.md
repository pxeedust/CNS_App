# CNS Outreach

A Django application for personalized email outreach, automated follow-ups, reply detection, sentiment analysis, and team activity tracking.

## What your teammate needs

For local testing, they need:

- Python 3.12 or newer
- A Gemini API key
- A Gmail or Google Workspace account with 2-Step Verification and an app password
- Git

Redis is **not required for the first local test**. The example development configuration runs Celery tasks eagerly in the Django process. Redis and a Celery worker should be used for shared or production deployments.

## Clone and install

```bash
git clone <your-github-repository-url>
cd CNS_App
python -m venv .venv
```

Activate the virtual environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS/Linux
source .venv/bin/activate
```

Install the dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Create the local environment file

Copy `.env.example` to `.env`:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS/Linux
cp .env.example .env
```

The `.env` file is ignored by Git. Never paste its values into a commit, issue, screenshot, or chat message.

Generate two different random secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Put the first value in `SECRET_KEY` and the second in `MAILBOX_ENCRYPTION_KEY`.

### Minimal local `.env`

```dotenv
DJANGO_ENV=development
DEBUG=True
SECRET_KEY=<first-random-value>
MAILBOX_ENCRYPTION_KEY=<second-random-value>

ALLOWED_HOSTS=localhost,127.0.0.1,[::1]
TIME_ZONE=Asia/Kolkata
DATABASE_URL=sqlite:///db.sqlite3
DATABASE_SSL_REQUIRE=False

GEMINI_API_KEY=<teammate-gemini-key>
GEMINI_MODEL=gemini-2.5-flash
GEMINI_REQUEST_TIMEOUT_SECONDS=60
GEMINI_MAX_RETRIES=3
ALLOW_STATIC_EMAIL_FALLBACK=False

EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
SMTP_TIMEOUT_SECONDS=30

IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USE_SSL=True
IMAP_TIMEOUT_SECONDS=30

CELERY_TASK_ALWAYS_EAGER=True
CELERY_RESULT_BACKEND=django-db
```

`EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` can remain blank. Outreach credentials are configured per user inside the application.

## Which Google credentials go where?

| Credential | Where it is configured | What it does |
|---|---|---|
| Gemini API key | `GEMINI_API_KEY` in `.env` | Generates personalized emails, follow-ups, and reply sentiment; Google Search grounding runs through Gemini |
| Gmail/Workspace address | **My Mailbox** inside the app | SMTP sender and IMAP inbox identity |
| Gmail app password | **My Mailbox** inside the app | Authenticates SMTP sending and IMAP reply scanning |
| Other Google Cloud API key | `GOOGLE_CLOUD_API_KEY` if future code needs it | Reserved for a non-Gemini Google service; currently unused |

The app does not use the Gmail API, so it does not need a Gmail API key or OAuth client for its current mail flow. It uses SMTP and IMAP with an app password.

Using separate restricted keys is recommended. A Gemini/AI Studio key may be limited by its project, enabled API, application restrictions, billing, or quota and should not be treated as a general Google Cloud key.

## Create the Gemini key

1. Open Google AI Studio and create an API key.
2. Put it in `.env` as `GEMINI_API_KEY`.
3. Do not add quotes unless the value genuinely contains spaces.
4. Restart Django and the Celery worker after changing it.

If the key is managed from Google Cloud, verify that its project and API restrictions allow the Gemini Generative Language API. A `403` generally indicates permissions or restrictions; a `429` generally indicates quota or rate limiting.

## Create the Gmail app password

1. Enable 2-Step Verification on the sender's Google account.
2. Create a Google app password for mail.
3. Start the application and sign in.
4. Open **My Mailbox**.
5. Enter the sender name, sender role, full mailbox address, and app password.

Use the generated app password, not the account's normal password. Some Google Workspace administrators disable app passwords; in that case the Workspace administrator must permit them or the mail integration must be migrated to OAuth.

Mailbox app passwords are encrypted in the database using `MAILBOX_ENCRYPTION_KEY`. Do not change that encryption key after credentials have been saved. For a deliberate key rotation, keep the previous value in `MAILBOX_ENCRYPTION_OLD_KEYS` until credentials have been rewritten.

## Initialize and run the app

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/> and sign in with the superuser account.

Recommended first test:

1. Configure **My Mailbox**.
2. Open **Templates** and create or review a campaign template.
3. Add one contact using an email address controlled by the tester.
4. Keep `ALLOW_STATIC_EMAIL_FALLBACK=False` so a Gemini problem cannot silently send generic mail.
5. Run the campaign for that single contact.
6. Open the contact thread and verify the generated body, generation mode, Message-ID, and delivery status.
7. Reply from the test recipient and run **Scan Replies**.

Do not begin testing with a real bulk contact list.

## Campaign templates

Application admins can manage templates through **Templates** in the navigation. Django superusers can also use Django Admin.

Each campaign includes:

- `name`
- `target_industry` — use `All` for all eligible contacts
- `subject_template`
- `email_template`

Supported placeholders include:

```text
{{first_name}}      {{last_name}}       {{full_name}}
{{title}}           {{company}}         {{industry}}
{{keywords}}        {{website}}         {{location}}
{{sender_name}}     {{sender_role}}      {{sender_email}}
{{email_body}}
```

`{{email_body}}` is the insertion point for Gemini-personalized copy. For example:

```text
Hello {{first_name}},

{{email_body}}

Best regards,
{{sender_name}}
{{sender_role}}
180 Degrees Consulting, IIT Kharagpur
```

Unknown placeholders are rejected when the template is saved.

## Why messages should no longer all look identical

Each outbound email now has a durable `OutboundEmail` record containing:

- the exact subject and body
- `Gemini AI`, `Campaign template`, `Built-in template`, or `Generation failed` provenance
- any Gemini generation error
- SMTP delivery state and error
- Message-ID and idempotency key
- campaign, sender, and recipient snapshots

If Gemini fails and `ALLOW_STATIC_EMAIL_FALLBACK=False`, the application records the failure and does **not** send an email. Static fallback should be enabled only when generic mail is intentionally acceptable.

## Background jobs

For a quick local test:

```dotenv
CELERY_TASK_ALWAYS_EAGER=True
```

Campaigns run inside the web process in this mode, so test only one or two contacts.

For normal shared development or production, use Redis and a Celery worker:

```dotenv
CELERY_TASK_ALWAYS_EAGER=False
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=django-db
```

Then run these in separate terminals:

```bash
python manage.py runserver
celery -A cns_app worker --loglevel=info
```

On Windows, Celery may require a compatible worker pool or a Linux/WSL/Docker environment for reliable production-like operation.

## Verification

Run before pushing or deploying:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py test
```

The current suite covers authorization, encrypted mailbox credentials, campaign targeting, personalization provenance, generation failures, idempotency, follow-up scoping, and reply-thread matching.

## Common problems

### Gemini says the key is missing

- Confirm the file is named exactly `.env`, not `.env.txt`.
- Confirm it is in the repository root beside `manage.py`.
- Use `GEMINI_API_KEY`, not only `GOOGLE_CLOUD_API_KEY`.
- Restart Django/Celery after editing `.env`.

### Gemini returns `403`

- Check API and application restrictions on the key.
- Check that the correct Google project owns the key.
- Confirm the Gemini Generative Language API is allowed.
- Do not use a key restricted to a different Google API.

### Gemini returns `429`

- Check the project's Gemini quota and billing tier.
- Reduce campaign size and retry later.
- The app already uses bounded retries; repeated failures are recorded without sending generic mail.

### Gmail authentication fails

- Use an app password rather than the normal Google password.
- Confirm 2-Step Verification is enabled.
- Remove spaces copied into the app password.
- Check whether the Workspace administrator permits app passwords and SMTP/IMAP.

### Campaign remains queued or does not start

- For local testing, set `CELERY_TASK_ALWAYS_EAGER=True`.
- Otherwise confirm Redis and the Celery worker are running.

## GitHub safety checklist

Before pushing:

```bash
git status
git diff --check
```

Confirm that none of these are staged:

- `.env`
- `db.sqlite3`
- real Apollo/contact exports
- generated email JSON files
- mailbox credentials

The repository contains `apollo_contacts.example.csv` with fake data. Use that only to verify the import format.
