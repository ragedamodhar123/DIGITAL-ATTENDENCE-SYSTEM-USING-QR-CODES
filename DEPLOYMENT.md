# Deploying the attendance system

This guide takes the app from "runs on my laptop through a temporary trycloudflare address"
to "runs every day at a fixed HTTPS address". Do the steps in order. Commands are for
Windows, run in the project folder with the virtual environment active.

## 1. Choose where it runs

Option A, recommended: a Windows PC or server on campus that stays on during class hours,
reached through a named Cloudflare Tunnel. No ports to open on the college network, HTTPS
handled by Cloudflare, a fixed address such as `attendance.yourcollege.edu`. Needs a domain
whose DNS is managed in a (free) Cloudflare account.

Option B: a college server that the IT team exposes on the internet, with Caddy in front
for HTTPS. Use this if the college does not want Cloudflare or already has a domain and
open port 443. See section 6.

Option C: a small cloud machine (any provider, 2 GB RAM is enough). The same steps work on
Linux; replace the `.bat` scripts with a systemd service. Keep the database on the same
machine and back it up off the machine.

Whichever you choose, the machine needs: Python 3.12, PostgreSQL, about 1 GB free disk
(more for backups), and a reliable connection. One process with 8 threads serves a class of
60 comfortably: each face check takes well under a second of CPU time, and students
scan at slightly different moments. Check this in the trial class (docs/admin-checklist.md).

## 2. Prepare the machine

1. Install Python 3.12, PostgreSQL and Git. Create the database user and databases as in
   the README (First-time setup, step 1). Use a new strong password, not the one from
   development.
2. Copy the project (git clone, or copy the folder without `venv`, `.env`, `logs`,
   `backups`, `models`). Create the virtual environment and install packages:
   `py -3.12 -m venv venv`, `venv\Scripts\activate`, `pip install -r requirements.txt`.
3. Download Bootstrap and the QR scanner (README setup step 6), and the face models:
   `flask --app wsgi download-face-models`.
4. Copy `.env.production.example` to `.env` and fill it in (section 3).
5. Build the database: `flask --app wsgi db upgrade`.
6. Create the admin: `flask --app wsgi create-admin --username admin --name "College Admin"`.
   Store the printed token in a password manager.
7. Run `flask --app wsgi check`. Every line must be OK, except WARN for TRUSTED_HOSTS
   until section 4 is done.

Do not run `seed-demo` on a production machine (the command refuses anyway).

## 3. The production .env

Start from `.env.production.example`. Important values:

- `FLASK_CONFIG=production`: secure cookies, HTTPS-only behaviour, log files, HSTS.
- `SECRET_KEY`: a new random value (`python -c "import secrets; print(secrets.token_hex(32))"`).
  Never reuse the development key. Changing it later logs everybody out.
- `DATABASE_URL`: the production database and its password.
- `TRUSTED_PROXY_COUNT=1`: the app sits behind cloudflared or Caddy.
- `TRUSTED_HOSTS`: your address, e.g. `attendance.yourcollege.edu`.
- `HOST=127.0.0.1` and `PORT=8000`: only the tunnel or proxy on this machine can reach the
  app directly.
- `PG_BIN`: PostgreSQL's bin folder if `flask check` cannot find pg_dump.
- `ATTENDANCE_ALLOWED_NETWORKS` (optional): the college's public IP range, to refuse scans
  from outside campus. Ask IT for it. Leave empty at first; turn on once things work.

Keep `.env` private: it holds the database password and the secret key.

## 4. Fixed HTTPS address with a named Cloudflare Tunnel (option A)

1. Create a free Cloudflare account and add your domain (Cloudflare explains how to change
   the domain's nameservers). Wait until Cloudflare shows the domain as active.
2. In the Cloudflare dashboard, open Zero Trust, then Networks, then Tunnels, and create a
   tunnel of type cloudflared, named for example `attendance`.
3. Choose Windows. Cloudflare shows an install command of the form
   `cloudflared.exe service install <TOKEN>`. Run it in a Command Prompt opened as
   administrator. This installs cloudflared as a Windows service that starts with Windows.
   The token is a secret: anyone with it can run your tunnel. Do not paste it into chats,
   documents or Git.
4. Add a public hostname for the tunnel: subdomain `attendance`, your domain, service type
   HTTP, URL `localhost:8000`.
5. Start the app: `python serve.py`. Open `https://attendance.yourcollege.edu` on a phone.
6. Put that host name in `TRUSTED_HOSTS` in `.env` and restart `serve.py`.
   `flask --app wsgi check` should now be all OK.

Cloudflare's dashboard labels change from time to time; if a step looks different, follow
their current "Create a tunnel (dashboard)" guide. cloudflared does not update itself on
Windows: install the latest version every few months. If the token ever leaks, refresh it
in the tunnel's settings and reinstall the service with the new command.

## 5. Keep it running

1. Start the app with Windows: create the scheduled task from the README (Production on
   Windows) for `scripts\start_server.bat`. Log in once after a restart, or ask IT to set
   the task to run whether or not a user is logged in.
2. Nightly backup: create the scheduled task from the README (Backups) for
   `scripts\backup_db.bat`. The next morning, check `logs\backup.log` and the `backups`
   folder.
3. Copy the `backups` folder to another disk or cloud storage at least weekly.
4. Practise one restore on a spare database before the term starts (README, Backups).
5. Windows settings: stop the PC from sleeping during class hours (Power settings), and set
   Windows Update active hours to the college day so it does not restart mid-class.

## 6. Option B: college server with Caddy

Use this when the server has a public address and the college domain points to it, with
ports 80 and 443 open (IT will know).

1. Download Caddy for Windows from caddyserver.com and put `caddy.exe` in a folder such as
   `C:\caddy`.
2. Create `C:\caddy\Caddyfile` with exactly this content (your domain on the first line):

   ```
   attendance.yourcollege.edu {
       reverse_proxy localhost:8000
   }
   ```

   Caddy gets and renews the HTTPS certificate automatically and passes the visitor's
   address on to the app.
3. Run `caddy run --config C:\caddy\Caddyfile` (as a service: ask IT, or use a scheduled
   task like `start_server.bat`).
4. `.env`: `TRUSTED_PROXY_COUNT=1` and `TRUSTED_HOSTS=attendance.yourcollege.edu`.

Never expose waitress (port 8000) directly to the internet: always put the tunnel or Caddy
in front, and keep `HOST=127.0.0.1`.

## 7. Before the first class of term

Follow `docs/admin-checklist.md` (accounts, enrolment, supervised face registration, a
trial class). Give faculty `docs/faculty-checklist.md` and students `docs/student-guide.md`.

## 8. Updating the app later

1. Run a backup: `flask --app wsgi backup-db`.
2. Stop `serve.py` (close its window or end the task).
3. Copy in the new code; `pip install -r requirements.txt`; `flask --app wsgi db upgrade`.
4. `flask --app wsgi check`, then start `serve.py` again.

Do updates outside class hours. If anything goes wrong, restore the backup from step 1 and
put the old code back.