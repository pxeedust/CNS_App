# CNS Outreach

A Django application for personalized email outreach, automated follow-ups, reply detection, sentiment analysis, and team activity tracking.

## 1. Google Cloud Console and Vertex AI setup

This is the primary setup path for using Gemini through Google Cloud billing.
Creating a Gemini Developer API key in Cloud Console does not switch that API to
Vertex AI. This application explicitly selects the Vertex AI backend.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select or
   create a project. Copy its **project ID**, not its display name or number.
2. Open **Billing** and link that project to the billing account holding your
   eligible, unexpired credits. Check the credit expiry and covered services.
3. Open **APIs & Services > Library**, search for **Vertex AI API** (also shown
   as Agent Platform in newer Console screens), and enable
   `aiplatform.googleapis.com`.
4. Under **IAM & Admin > IAM**, the account used locally needs
   **Vertex AI User / Agent Platform User** (`roles/aiplatform.user`) and
   **Service Usage Consumer** (`roles/serviceusage.serviceUsageConsumer`),
   or equivalent permissions. Enabling an API additionally requires
   `serviceusage.services.enable`.
5. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
   Reopen your terminal after installation.

After creating `.env` in step 3 below, configure:

```dotenv
GOOGLE_GENAI_USE_VERTEXAI=True
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.6-flash
```

In this mode the app uses Application Default Credentials (ADC), not the
Developer API key. Enable `aiplatform.googleapis.com` in that project and ensure
it is linked to the billing account with your eligible, unexpired Cloud credits.
The Google Cloud $300 welcome credit does not cover Gemini Developer API / AI
Studio usage; see https://ai.google.dev/gemini-api/docs/billing.

For local Windows development, run:

```powershell
gcloud.cmd auth login
gcloud.cmd config set project your-project-id
gcloud.cmd services enable aiplatform.googleapis.com --project your-project-id
gcloud.cmd auth application-default login
gcloud.cmd auth application-default set-quota-project your-project-id
```

Replace `your-project-id` in every command with your actual project ID. Sign in
with the account that has access to that project. `auth login` authenticates the
CLI; `auth application-default login` separately authenticates the Python app.
On macOS/Linux use `gcloud` instead of `gcloud.cmd`.

Official reference: [Vertex AI setup and authentication](https://cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart).

The signed-in identity needs `roles/aiplatform.user` and permission to consume
services in the project. Restart Django and any Celery worker after configuring
ADC. No service-account JSON download is needed for this local login flow.
On a hosted deployment, use an attached service account or workload identity.

The API-key instructions below describe the alternative Developer API backend
(`GOOGLE_GENAI_USE_VERTEXAI=False`). Gmail mailbox setup applies to both backends.

## Requirements

Local development requires:

- Python 3.12 or newer
- Google Cloud CLI and a project with Vertex AI enabled (or a Gemini Developer API key for the optional backend)
- A Gmail or Google Workspace account with 2-Step Verification and an app password
- Git

Redis is **not required for the first local test**. The example development configuration runs Celery tasks eagerly in the Django process. Redis and a Celery worker should be used for shared or production deployments.

## 2. Clone and install

```bash
git clone https://github.com/pxeedust/CNS_App.git
cd CNS_App
python -m venv .venv
```

On Windows, use the environment's Python directly so PowerShell script
execution policy does not block activation:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

All `python manage.py ...` commands below assume your virtual environment is
active. On Windows, replace `python` with `.\.venv\Scripts\python.exe`.

## 3. Create the local environment file

Copy `.env.example` to `.env`:

```powershell
# Windows PowerShell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
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

GOOGLE_GENAI_USE_VERTEXAI=True
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.6-flash
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

## Credential configuration

| Credential | Where it is configured | What it does |
|---|---|---|
| Google Cloud ADC | Local `gcloud auth application-default login` | Authenticates Vertex AI to the configured project |
| Gemini API key (optional backend) | `GEMINI_API_KEY` in `.env` | Generates personalized emails, follow-ups, and reply sentiment; Google Search grounding runs through Gemini |
| Gmail/Workspace address | **My Mailbox** inside the app | SMTP sender and IMAP inbox identity |
| Gmail app password | **My Mailbox** inside the app | Authenticates SMTP sending and IMAP reply scanning |
| Other Google Cloud API key | `GOOGLE_CLOUD_API_KEY` if future code needs it | Reserved for a non-Gemini Google service; currently unused |

The app does not use the Gmail API, so it does not need a Gmail API key or OAuth client for its current mail flow. It uses SMTP and IMAP with an app password.

Using separate restricted keys is recommended. A Gemini/AI Studio key may be limited by its project, enabled API, application restrictions, billing, or quota and should not be treated as a general Google Cloud key.

### Optional: Gemini Developer API instead of Vertex AI

1. Set `GOOGLE_GENAI_USE_VERTEXAI=False` in `.env`.
2. Open Google AI Studio and create an authorization API key for your project.
3. Put it in `.env` as `GEMINI_API_KEY`.
4. Do not add quotes unless the value genuinely contains spaces.
5. Restart Django and the Celery worker after changing it.

If the key is managed from Google Cloud, verify that its project and API restrictions allow the Gemini Generative Language API. A `403` generally indicates permissions or restrictions; a `429` generally indicates quota or rate limiting.

## 4. Gmail mailbox setup

1. Enable 2-Step Verification on the sender's Google account.
2. Create a [Google app password](https://myaccount.google.com/apppasswords) for mail.
3. Start the application and sign in.
4. Open **My Mailbox**.
5. Enter the sender name, sender role, full mailbox address, and app password.

Use the generated app password, not the account's normal password. Some Google Workspace administrators disable app passwords; in that case the Workspace administrator must permit them or the mail integration must be migrated to OAuth.

Mailbox app passwords are encrypted in the database using `MAILBOX_ENCRYPTION_KEY`. Do not change that encryption key after credentials have been saved. For a deliberate key rotation, keep the previous value in `MAILBOX_ENCRYPTION_OLD_KEYS` until credentials have been rewritten. Normally leave `MAILBOX_ENCRYPTION_OLD_KEYS` blank; do not repeat the current key there. For rotation, back up the database and keys, retain the previous key in the comma-separated old-keys list, and re-save every mailbox password using the new current key before removing old keys.

## 5. Initialize and run the app

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/> and sign in with the superuser account.

### Safe smoke test

1. Configure **My Mailbox**.
2. Open **Templates** and create or review a campaign template.
3. Add one contact using a controlled test email address.
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

For **Templates > Create Template**, use:

- Name: `Partnership Outreach`
- Target industry: `All`
- Subject template:

```text
Exploring a collaboration with {{company}} | 180 Degrees Consulting, IIT Kharagpur
```

- Email template:

```text
{{email_body}}
```

The AI-generated `email_body` is a **complete email**, including greeting,
paragraphs, call request, and signature. Adding another greeting, CTA, or
signature around this placeholder duplicates those sections. Existing saved
templates live in the database; pulling code does not update them. Edit an old
wrapper template in the UI and replace its body with the single placeholder.

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

## Production deployment

For Azure for Students, follow the [Azure deployment guide](deploy/azure/README.md),
including student-credit limits, VM creation and the Azure-provided hostname.

For a shared Oracle Cloud VM with HTTPS, PostgreSQL, Redis and a Celery worker,
follow the [Oracle deployment guide](deploy/oracle/README.md). The repository
includes a Dockerfile, Compose stack and deployment script for that setup.

Production should set at least:

```dotenv
DJANGO_ENV=production
DEBUG=False
SECRET_KEY=<strong-production-secret>
MAILBOX_ENCRYPTION_KEY=<separate-strong-encryption-secret>
ALLOWED_HOSTS=<application-hostname>
CSRF_TRUSTED_ORIGINS=https://<application-hostname>
DATABASE_URL=<persistent-postgresql-url>
DATABASE_SSL_REQUIRE=True
CELERY_TASK_ALWAYS_EAGER=False
CELERY_BROKER_URL=<persistent-redis-url>
```

The deployment build must run:

```bash
python manage.py collectstatic --no-input
python manage.py migrate
```

The included `build.sh` performs both operations. Do not rely on the default SQLite database on an ephemeral hosting platform: user accounts, contacts, campaign history, and encrypted mailbox settings can disappear when the instance is rebuilt. Use persistent PostgreSQL for a hosted installation.

## Verification

Run before pushing or deploying. Tests should use the Developer API configuration
with no real credentials; mocked generation tests must not inherit local Vertex
settings. In PowerShell, set these for the test terminal:

```powershell
$env:GOOGLE_GENAI_USE_VERTEXAI="False"
$env:GEMINI_API_KEY=""
$env:GOOGLE_API_KEY=""
```

Then run:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py test
```

Close that terminal after testing, or remove the three environment overrides
before running the app. Process environment variables override `.env`.

The current suite covers authorization, encrypted mailbox credentials, campaign targeting, personalization provenance, generation failures, idempotency, follow-up scoping, and reply-thread matching.

## Common problems

### Vertex authentication fails

Run `gcloud.cmd auth login` again for an expired CLI login. Run
`gcloud.cmd auth application-default login` if Python reports missing or expired
ADC. Confirm the account has access to the configured project and repeat
`gcloud.cmd auth application-default set-quota-project your-project-id`.

### Prepayment credits depleted

This message belongs to Gemini Developer API billing. To use the Cloud project
backend, verify `GOOGLE_GENAI_USE_VERTEXAI=True`, the project ID, ADC login, and
restart both Django and Celery. Vertex requests should go to
`aiplatform.googleapis.com` with your project in the URL. Google Cloud welcome
credits do not cover AI Studio / Developer API usage. Do not expect a new API key
to change the billing route.

### Invalid structured email JSON / MAX_TOKENS

The email generators use a 4096-token output cap and LOW thinking for Gemini 3
models. Incomplete or blocked responses are rejected rather than sent. Update
the code and restart all processes if you were using the older 900/1200-token
caps. HTTP 200 alone does not mean the returned email is complete.

### Contacts remain on Sending after a restart

The current implementation reserves campaign contacts before generating mail,
so `Sending` also includes waiting for generation. Interrupting eager-mode
Django can leave reservations behind. The next initial campaign reclaims claims
older than 30 minutes by default; this is not a periodic cleanup job. Before
manually resetting anything, stop the affected worker and review delivery
records. Never reset a confirmed sent email or blindly retry an uncertain SMTP
attempt. The contact thread preserves generation and delivery errors.

### Informational SDK logs

`AFC is enabled` and the project/location precedence notice are informational.
In Vertex mode the explicit project/location select ADC authentication even if
a Developer API key remains in the environment.


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
