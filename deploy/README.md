# Deploying to hilltop.bthinkx.com

1. Point the DNS A record for `hilltop.bthinkx.com` at the server.
2. Put the code in `/srv/hilltop`, then:
   ```
   python -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.production.example .env   # set SECRET_KEY
   .venv/bin/python manage.py migrate
   .venv/bin/python manage.py collectstatic --noinput
   .venv/bin/python manage.py check --deploy
   ```
3. Install `deploy/hilltop.service` into `/etc/systemd/system/`, then `systemctl enable --now hilltop`.
4. Issue a certificate (`certbot certonly --nginx -d hilltop.bthinkx.com`), install `deploy/nginx.conf` and reload nginx.
5. The database is SQLite (`/srv/hilltop/db.sqlite3`). The service user must own both the file and the `/srv/hilltop` folder, because SQLite writes a journal file next to the database. Back it up regularly, e.g. with `sqlite3 db.sqlite3 ".backup /backups/hilltop-$(date +%F).sqlite3"`.
6. Make `/srv/hilltop/media` writable by the service user and include it in backups together with the database.

To switch to PostgreSQL later, set `DATABASE_URL` in `.env` and migrate the data; no code change is needed.

Uploaded photos are served through Django views (not directly by nginx), so no `/media/` location is needed.
`manage.py` still defaults to the development settings; the server uses `DJANGO_SETTINGS_MODULE` from `.env`.
