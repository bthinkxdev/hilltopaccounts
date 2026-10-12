# Deploying to hilltop.bthinkx.com

1. Point the DNS A record for `hilltop.bthinkx.com` at the server.
2. Put the code in `/srv/hilltop`, then:
   ```
   python -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.production.example .env   # set SECRET_KEY and the database password
   .venv/bin/python manage.py migrate
   .venv/bin/python manage.py collectstatic --noinput
   .venv/bin/python manage.py check --deploy
   ```
3. Install `deploy/hilltop.service` into `/etc/systemd/system/`, then `systemctl enable --now hilltop`.
4. Issue a certificate (`certbot certonly --nginx -d hilltop.bthinkx.com`), install `deploy/nginx.conf` and reload nginx.
5. Make `/srv/hilltop/media` writable by the service user and include it in backups together with the database.

Uploaded photos are served through Django views (not directly by nginx), so no `/media/` location is needed.
`manage.py` still defaults to the development settings; the server uses `DJANGO_SETTINGS_MODULE` from `.env`.
