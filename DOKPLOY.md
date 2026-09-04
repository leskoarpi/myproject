# Deploying with Dokploy

The repository ships a compose file written for Dokploy:
**`docker-compose.dokploy.yml`**. Everything below is UI work — no SSH, and
nothing to edit in the code.

---

## 1. Create the application

In Dokploy: **Create Service → Compose**, pick the GitHub repository and the
`master` branch, then set:

| Field | Value |
| --- | --- |
| Compose Type | Docker Compose |
| Compose Path | `./docker-compose.dokploy.yml` |

## 2. Environment

Open the **Environment** tab and paste this, replacing the two secrets and the
domain:

```ini
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_DEBUG=false
INSTALL_DEV=false
ALLOW_DESTRUCTIVE_MAINTENANCE=false

DJANGO_SECRET_KEY=<paste a long random value>
POSTGRES_PASSWORD=<paste a different long random value>
POSTGRES_DB=deakkoli
POSTGRES_USER=deakkoli

DJANGO_ALLOWED_HOSTS=koli.example.hu
DJANGO_CSRF_TRUSTED_ORIGINS=https://koli.example.hu
```

Generate each secret separately — run this twice and use a different value
for each. `LC_ALL=C` is required: without it, macOS `tr` reads `/dev/urandom`
as UTF-8 text and fails with "Illegal byte sequence".

```bash
LC_ALL=C tr -dc 'A-Za-z0-9_-' < /dev/urandom | head -c 48; echo
```

`openssl rand -base64 48 | tr -d '\n=+/' | head -c 48` works too and has no
locale caveat.

Keep a copy of `POSTGRES_PASSWORD` somewhere safe. PostgreSQL bakes it into
the data volume the first time it starts and ignores it afterwards, so
changing it later does not update the database — it locks the app out of it.
`DJANGO_SECRET_KEY` can be rotated freely; it only invalidates open sessions.

Two rules worth remembering, because mixing them up is the classic failure:
`DJANGO_ALLOWED_HOSTS` takes **bare hostnames**, comma-separated, no scheme.
`DJANGO_CSRF_TRUSTED_ORIGINS` takes **full origins**, with `https://`. The
first controls whether pages load at all; the second whether forms submit.

Anything you add here reaches Django — the compose file loads the whole
environment, so `EMAIL_HOST`, `MORNING_CUTOFF_HOUR` and the rest work without
touching the file.

## 3. Domain

Under **Domains**, add your domain:

| Field | Value |
| --- | --- |
| Service Name | `nginx` |
| Container Port | `80` |
| HTTPS | on (Let's Encrypt) |

`nginx` is the entry point, not `web`: it serves `/static/` and `/media/` off
the shared volumes and proxies everything else to Gunicorn. Dokploy writes the
Traefik labels; the compose file already puts `nginx` on `dokploy-network`.

Point the domain's DNS `A` record at the server before deploying, or the
certificate cannot be issued.

## 4. Deploy

Press **Deploy**. The first build takes a few minutes.

On every start the `web` service runs migrations, `collectstatic` and the
reference-data seed, all idempotent — so a redeploy after a `git push` needs
no extra step, and neither does a schema change.

## 5. Create your administrator

Once the deploy is green, open a **Terminal** on the `web` container from the
Dokploy UI:

```bash
python manage.py createsuperuser
```

Then grant it the Admin role, which is what actually carries the permissions
here — Django's superuser flag alone carries none:

```bash
python manage.py shell -c "from django.contrib.auth import get_user_model; U=get_user_model(); U.objects.filter(is_superuser=True).update(role='admin'); print('done')"
```

Sign in at your domain, then set up the school year, rooms, groups and
teachers under **Adatkezelés**.

---

## Updating

Push to `master` and press **Deploy** — or turn on **Auto Deploy** in the
General tab, which redeploys on every push via webhook.

## Backups

Dokploy's own scheduled backups can dump the `db` service to S3 or local
storage; set that up under the database's **Backups** tab. The equivalent by
hand, from a terminal on the `db` container:

```bash
pg_dump -U deakkoli deakkoli | gzip > /tmp/backup-$(date +%F).sql.gz
```

Whatever you choose, keep a copy off the server.

---

## If something goes wrong

**Deploy fails with `set DJANGO_SECRET_KEY in the Environment tab`.** A
required variable is missing. The compose file names each one it needs in the
error text.

**`400 Bad Request` on every page.** The domain is not in
`DJANGO_ALLOWED_HOSTS`. Add it — bare hostname, no `https://`.

**Pages load, but every form is rejected.** The domain is missing from
`DJANGO_CSRF_TRUSTED_ORIGINS`, or is listed there without the `https://`
prefix.

**Login does nothing — the form returns to the login page with no error.**
The site is being served over plain http while cookies are marked secure, so
the browser discards the session. Either finish the certificate, or set
`DJANGO_SECURE_COOKIES=false` in the Environment tab while you sort it out,
and remember to remove it afterwards.

**`502` from Traefik.** The `nginx` service is not up, or the domain points at
the wrong service. Check the **Logs** tab for `nginx` and `web`, and confirm
the domain targets `nginx` on port `80`.

**Static files 404 or the page has no styling.** `collectstatic` did not run
against the current code — redeploy, and check the `web` logs for the
"static files copied" line.

**`password authentication failed for user "deakkoli"`.** `POSTGRES_PASSWORD`
was changed after the database volume was created. PostgreSQL sets that
password only when it first initialises its data directory and ignores it
afterwards. Restore the previous value, or delete the volume if the database
is still empty.
