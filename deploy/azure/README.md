# Deploy CNS Outreach on Azure for Students

Run the existing Docker Compose stack on one Ubuntu VM: Django, PostgreSQL,
Redis, Celery and Caddy. Everyone signs in at the same HTTPS address; your laptop
does not need to stay on. No Azure SQL or managed Redis service is required.

## 1. Activate the student subscription

Register at [Azure for Students](https://azure.microsoft.com/en-us/free/students/)
using your eligible college identity. Complete student verification, then open
<https://portal.azure.com> and confirm **Azure for Students** appears under
**Subscriptions**. The normal Azure free trial is a different signup flow.

Microsoft currently advertises $100 credit usable within 12 months, no credit
card required, plus selected free services for eligible customers. This does
not make every VM, disk or IP address free. Keep the student spending limit;
do not upgrade to pay-as-you-go just to follow this guide.

For this complete stack, start with **at least 4 GiB RAM** as an engineering
recommendation, then monitor memory and workload. A `Standard_B2s` (if available)
or another economical 4 GiB Ubuntu-compatible size is suitable to evaluate.
It is **not claimed to be included in the free VM allowance**. The smaller
free-eligible VM sizes can be too constrained for the current multi-process
stack. Do not assume the student credit will cover a full year of this setup.

Review the regional compute estimate, disk cost, public IPv4 cost and outbound
transfer before creating the VM. Use **Cost Management > Cost analysis** after
deployment and configure budget alerts if available. Budget alerts alone do not
stop spending. Google Vertex/Gemini charges remain on your Google project.

## 2. Create the VM

In **Virtual machines > Create > Azure virtual machine**, use:

| Setting | Value |
| --- | --- |
| Subscription | Azure for Students |
| Resource group | Create `cns-outreach-rg` |
| VM name | `cns-outreach` |
| Region | An allowed region near the team with an affordable available size |
| Availability | No infrastructure redundancy required for this single-VM setup |
| Image | Ubuntu Server 24.04 LTS, x64 for an x64 VM size |
| Size | An available economical size with at least 4 GiB RAM; review its cost |
| Authentication | SSH public key |
| Username | `azureuser` |
| Key | Generate a new key pair, or supply your public key |
| OS disk | A modest disk, e.g. 32 GiB; compare Standard SSD and eligible allowances |
| Network | New virtual network/subnet and a Standard public IPv4 on the VM NIC |

For this setup, use direct SSH and the attached public IP; additional Bastion,
NAT Gateway, load balancer or managed database resources are unnecessary. Check
optional paid monitoring/backup services before enabling them. Keep the VM on
when your team needs it; an auto-shutdown schedule takes the website offline.

Click **Review + create**, inspect the resources and costs, then **Create**.
Download the private key when offered and store it securely on your computer.
See [Microsoft's Linux connection guide](https://learn.microsoft.com/en-us/azure/virtual-machines/linux-vm-connect).

Under the VM's **Networking / Network settings**, configure its network security
group inbound rules:

- TCP 22: your own public IP address with `/32` as the source.
- TCP 80 and 443: Internet as the source, for the website and certificate issuance.
- Keep PostgreSQL 5432, Redis 6379 and Gunicorn 8000 closed to the Internet.

Retain outbound access for DNS, HTTPS, SMTP 587 and IMAP 993. The attached public
IP supplies outbound connectivity for this single-VM arrangement. Azure permits
[authenticated SMTP on 587](https://learn.microsoft.com/en-us/azure/virtual-network/troubleshoot-outbound-smtp-connectivity);
Google authentication and mailbox limits still apply.

## 3. Choose the web address

Open the VM's **Public IP address** resource, then **Configuration**, and set a
unique **DNS name label**, such as `cns-outreach-yourteam`. Save and copy the
actual fully qualified DNS name Azure displays. It typically ends in
`.cloudapp.azure.com`; do not guess it because Azure can include a generated
scope component. This avoids purchasing a custom domain. The public IP itself
may still consume credit. See [Azure DNS name labels](https://learn.microsoft.com/en-us/azure/virtual-machines/create-fqdn).

Use that exact hostname as `APP_DOMAIN` and `ALLOWED_HOSTS`, and prefix it with
`https://` for `CSRF_TRUSTED_ORIGINS` later. Caddy will request an HTTPS certificate
when the hostname resolves and ports 80/443 are accessible.

## 4. Connect and install Docker

From Windows PowerShell, replace the key path and IP:

```powershell
ssh -i "C:\Users\YOUR_USER\Downloads\cns-outreach_key.pem" azureuser@YOUR_VM_IP
```

Run the remaining commands in the Ubuntu SSH session. Install Docker Engine and
the Compose plugin using [Docker's official Ubuntu apt repository instructions](https://docs.docker.com/engine/install/ubuntu/#install-using-the-apt-repository),
then:

```bash
sudo systemctl enable --now docker
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/pxeedust/CNS_App.git
cd CNS_App
```

The remote checkout must contain the deployment files prepared locally. If they
have not been pushed yet, transfer the reviewed source files over SSH first.
Do not upload the entire local working folder, which contains secrets and data.

## 5. Configure and launch

The files under `deploy/oracle` are ordinary Linux/Docker files and also work on
Azure. Their directory name does not allocate or connect to Oracle resources.

```bash
umask 077
cp deploy/oracle/production.env.example .env.production
mkdir -p secrets backups
python3 -c 'import secrets; print(secrets.token_urlsafe(64)); print(secrets.token_urlsafe(64)); print(secrets.token_hex(32))'
nano .env.production
```

Put the generated values in `SECRET_KEY`, `MAILBOX_ENCRYPTION_KEY` and
`POSTGRES_PASSWORD`, respectively. Set the three hostname/origin variables from
step 3. For a migration, retain your existing mailbox encryption key and follow
the data-transfer instructions below before using the new site.

Configure Google authentication using
[the shared production credential instructions](../oracle/README.md#3-configure-production-secrets).
An Azure VM does not inherit your laptop's Google login. Use a dedicated service
identity for Vertex, or explicitly configure the supported Developer API mode.
Your existing Gmail app passwords are configured through **My Mailbox** after
login. Keep production configuration and Google credentials out of Git.

```bash
sudo bash deploy/oracle/deploy.sh
sudo docker compose --env-file .env.production exec web python manage.py createsuperuser
sudo docker compose --env-file .env.production logs --tail=80 web worker proxy
curl -I https://YOUR_AZURE_HOSTNAME/login/
```

Open `https://YOUR_AZURE_HOSTNAME`, sign in and use **Team** to add individual
accounts. Verify the site from a second device. Follow the root README's
controlled-recipient test for AI generation, sending and reply scanning.
Do not test by sending a real bulk campaign.

If keeping existing local accounts/contacts/history, follow
[the data-transfer procedure](../oracle/README.md#5-optional-transfer-your-existing-local-data)
before creating new production accounts. Replace `ubuntu` and the Oracle key
path in its SCP example with `azureuser` and your Azure key path. The export
contains sensitive mailbox credentials; never commit it.

## 6. Operate the app

Use [the shared backup and update procedure](../oracle/README.md#6-backups-and-updates).
Keep backups and encryption keys outside the VM as well. Docker volumes survive
container replacement but not disk deletion. Do not use `docker compose down -v`
on the production stack.

If the site times out, check DNS, VM state and NSG ports. For a 502, inspect web
and proxy logs. For email failures, inspect worker logs and Google credentials.
Watch disk usage and memory with `df -h`, `free -h`, and `sudo docker stats`.

Stopping/deallocating the VM makes the site unavailable and does not necessarily
stop disk/public IP charges. Back up before removing any resources. The student
offer is time/credit limited; plan continuity before those limits are reached.

These are prepared deployment instructions. Actual Azure quota, billing,
container startup, certificates and Google/Gmail access remain to be verified
on your VM; no Azure resources have been created by this change.
