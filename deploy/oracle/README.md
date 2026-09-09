# Oracle Cloud deployment

This runs Django/Gunicorn, a Celery worker, PostgreSQL, Redis, and Caddy on one
Ubuntu VM. Team members use one HTTPS address and their own app accounts.
Your laptop can be switched off. Gmail and Google AI remain external services.
Follow-ups and reply scans retain their existing UI-triggered behavior; this
deployment does not introduce a new periodic sending schedule.

## 1. Create the free infrastructure

Create your account at <https://www.oracle.com/cloud/free/> and complete Oracle's
identity/payment verification yourself. Select your home region carefully.

As checked on September 8, 2026, Oracle documents **2 OCPUs / 12 GB total A1 RAM**
and **200 GB combined boot/block storage** in its Always Free allowance. Older
guides showing 4 OCPUs / 24 GB can be outdated. Check the limits and cost estimate
in your own account before creating anything. Existing resources count too.
Free A1 capacity can be unavailable, and idle instances can be reclaimed.
See [Oracle's current limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

In **Compute > Instances > Create instance**, select:

- Name: `cns-outreach`.
- Always Free eligible Ubuntu 24.04, Arm-compatible image.
- Shape: `VM.Standard.A1.Flex`, 2 OCPUs and 12 GB RAM, within your total allowance.
- Boot volume: 50 GB; keep the free-eligible defaults for performance.
- A public subnet, internet gateway, default route to that gateway, and public IPv4.
- Save the generated SSH private key securely, or supply your existing public key.

Use the subnet security list or a network security group to allow inbound TCP
80 and 443 from `0.0.0.0/0`, and TCP 22 from your own public IP with `/32`.
Keep outbound internet access for DNS, HTTPS, Gmail SMTP 587, and IMAP 993.
Do not expose 8000, 5432, or 6379. Retain any required OCI platform rules.
If a host firewall is enabled, permit web traffic there too; Docker-published
ports have [special firewall behavior](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations).
Do not flush the VM's firewall rules.

If creation reports no capacity, try another availability domain in your home
region or retry later. A paid shape is not a free fallback.

## 2. Set up the address and Docker

Point a domain/subdomain's DNS A record at the VM's public IPv4. A subdomain your
team already owns is suitable. A free DNS hostname can also work if it resolves
to your VM and supports public certificate issuance. Oracle does not provide a
custom domain registration with this setup. Remove incorrect AAAA records if
IPv6 isn't configured. Use a hostname, not a bare IP, with the supplied config.

Connect from Windows PowerShell (replace placeholders):

```powershell
ssh -i "C:\path\oracle.key" ubuntu@YOUR_VM_IP
```

On the VM, install Docker Engine and the Compose plugin using
[Docker's official Ubuntu apt-repository instructions](https://docs.docker.com/engine/install/ubuntu/#install-using-the-apt-repository).
Then:

```bash
sudo systemctl enable --now docker
sudo apt-get install -y git
git clone https://github.com/pxeedust/CNS_App.git
cd CNS_App
```

The checkout must include this deployment's files. If they haven't been pushed,
transfer the updated source through SSH first. Do not transfer your entire local
folder with its `.env`, database, or exports as a source-code bundle.

## 3. Configure production secrets

```bash
umask 077
cp deploy/oracle/production.env.example .env.production
mkdir -p secrets backups
python3 -c 'import secrets; print(secrets.token_urlsafe(64)); print(secrets.token_urlsafe(64)); print(secrets.token_hex(32))'
nano .env.production
```

Use the three generated values for `SECRET_KEY`, `MAILBOX_ENCRYPTION_KEY`, and
`POSTGRES_PASSWORD`. Set `APP_DOMAIN`, `ALLOWED_HOSTS`, and
`CSRF_TRUSTED_ORIGINS` to your real hostname/origin. Do not include `https://` in
the first two. Preserve your existing mailbox encryption key if migrating data.
Keep `.env.production` and the encryption keys in a secure off-VM backup.
The database password must be URL-safe; the generated hexadecimal value is.
Changing it later also requires changing the PostgreSQL role password.

Compose supplies the internal database/broker URLs and disables eager jobs.
Database TLS is disabled only for the private Docker connection on this VM;
PostgreSQL has no published host port. HTTPS stays enabled for users.

For Vertex AI, set the same project, location and working model as your local
app. Oracle hosting does not include Google AI credits. Your Windows ADC login
does not automatically exist on this server.

Configure credentials for a dedicated Google service account with Vertex AI User
and Service Usage Consumer permissions on the project. Prefer an established
workload identity federation configuration if your organization has one. For a
small deployment without federation, a service-account JSON key is a practical
alternative if your Google organization permits key creation. Store it only as
`secrets/google-adc.json`, transfer it over SSH, and protect it:

```bash
sudo chown -R 10001:10001 secrets
sudo chmod 700 secrets
sudo chmod 400 secrets/google-adc.json
```

The app and worker run as UID 10001 and mount that directory read-only. Never
commit the key. Enable Vertex AI in that project and configure its billing.
See [Google's production authentication guidance](https://cloud.google.com/docs/authentication/set-up-adc-production).

Alternatively, set `GOOGLE_GENAI_USE_VERTEXAI=False`, provide `GEMINI_API_KEY`,
and remove `GOOGLE_APPLICATION_CREDENTIALS` for the Developer API backend.
That changes your billing route; it does not use Vertex credits.

## 4. Launch and create accounts

Run in the repository root on the VM:

```bash
sudo bash deploy/oracle/deploy.sh
sudo docker compose --env-file .env.production exec web python manage.py createsuperuser
sudo docker compose --env-file .env.production logs --tail=80 web worker proxy
curl -I https://YOUR_HOSTNAME/login/
```

The script builds the image, waits for the database/broker, runs deployment
checks and migrations, then starts the app, worker and proxy. Caddy obtains and
renews the certificate automatically when DNS and inbound ports are correct.
See [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).
The services restart automatically after a VM reboot.

Open `https://YOUR_HOSTNAME`, sign in, and use **Team** to create each colleague's
account. Assign ordinary members their appropriate role; reserve admin access
for people who manage the team. Each person configures **My Mailbox**. Verify
login, static assets, and access from a second device. Use the root README's
single-controlled-recipient smoke test to verify AI, mail sending and reply
scanning before starting real campaigns. Infrastructure startup alone does not
verify Google credentials or Gmail connectivity.

## 5. Optional: transfer your existing local data

For a fresh database, skip this section. For a transfer, do this **before**
creating new production accounts or using the website. Stop local Django and
Celery, finish/review pending sends, and back up `db.sqlite3` and your local
encryption keys. Keep both installations quiet during transfer.

Export locally with the Python environment used for the app:

```powershell
python manage.py dumpdata auth.user auth.group outreach --natural-foreign --natural-primary --indent 2 --output deployment-data.json
scp -i "C:\path\oracle.key" deployment-data.json ubuntu@YOUR_VM_IP:CNS_App/deployment-data.json
```

This export includes decrypted mailbox app passwords. Keep it private, never
commit it, and remove both copies once the migration is verified. It transfers
users and outreach records, not active sessions or the queued Celery jobs.
On the VM, after the first deployment has initialized the schema:

```bash
chmod 600 deployment-data.json
sudo docker compose --env-file .env.production stop web worker
sudo docker compose --env-file .env.production run --rm -T setup python manage.py loaddata --format=json - < deployment-data.json
sudo docker compose --env-file .env.production up -d web worker
```

Import only into a fresh destination; do not merge two active installations this
way. Verify account/contact/history counts, sign in with an existing account,
and verify mailbox settings. Review any pre-existing `Sending` records before
retrying campaigns so uncertain deliveries are not sent twice. Do not restart
the local worker once production is in use.

## 6. Backups and updates

Before updates and at least daily during use, create a database backup on the VM:

```bash
umask 077
mkdir -p backups
sudo docker compose --env-file .env.production exec -T db pg_dump -U cns -d cns -Fc > "backups/cns-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Copy backups off the VM along with securely stored production secrets. Local
Docker volumes survive container replacement, but not deletion of the boot
disk. Test restoration into a separate empty PostgreSQL database before relying
on backups. A custom-format dump restores with `pg_restore -U cns -d cns`;
stop web/worker first, restore only to the intended empty database, and use the
same mailbox encryption keys. Never run `docker compose down -v` on your live
installation: it deletes the persistent volumes.

For an update, wait for campaigns to finish, back up, pull the reviewed code,
and rerun `sudo bash deploy/oracle/deploy.sh`. It briefly stops the web app and
allows the worker up to 16 minutes to finish before applying migrations. If
checks/migrations fail, correct the error before restarting application services.
Container tags follow their indicated major versions; review image updates and
database upgrade requirements before changing them.

## Validation status

Deployment configuration can be checked locally, but the real container build,
Arm execution, certificate issuance, PostgreSQL migration and Google/Gmail
connectivity must be verified on the provisioned VM. This repository does not
create an Oracle account or allocate cloud resources automatically.
