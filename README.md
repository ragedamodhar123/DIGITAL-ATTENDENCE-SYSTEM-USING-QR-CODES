# Digital Attendance System (Dynamic QR + Face Verification)

A Flask web app for college attendance. Faculty display a QR code that changes
every 10 seconds; students scan it from their own logged-in phone, pass a face
check, and only then are marked present.

Roles: Admin (works through Postman, no admin pages), Faculty, Student.


## Progress

Done
- Module 0, Part 1: app factory, configuration, extensions, /health check
- Module 0, Part 2: User, Section, Course, CourseFaculty, Enrollment models; password and API-token hashing
- Module 0, Part 3: AttendanceSession, PendingCheckin, AttendanceRecord, AttendanceAudit, ScanLog, FaceEmbedding models
- Module 0, Part 4: Alembic migrations (with the audit trigger), `flask create-admin`, `flask seed-demo`
- Module 1, Part 1: auth service (login, password rules, password change, admin token check);
  startup guard that keeps test and development databases apart
- Module 1, Part 2: route protection (role, first-login and face-enrolment gates), admin token
  decorator, rate-limit keys, consistent JSON/HTML error responses
- Module 1, Part 3: login, logout and change-password pages; student and faculty dashboards;
  login rate limits; security headers; placeholder face-registration page
- Module 2, Part 1: input validation schemas; user service (create faculty and students,
  bulk-create students, edit, deactivate/reactivate, issue temporary passwords)
- Module 2, Part 2: academic service (sections, courses, faculty assignment, enrolment by
  username list or whole section, drop, course roster)
- Module 2, Part 3: admin JSON API under /api/admin (token only, CSRF-exempt); faculty
  course roster page with "New password" for students; Postman collection and environment
- Module 3, Part 1: face service with OpenCV (YuNet finds faces, SFace compares them):
  safe image decoding, quality checks, registration from 3 photos, verification;
  `flask download-face-models` with checksum verification
- Module 3, Part 2: face registration page (phone camera, 3 guided photos, each checked
  as it is taken); JSON endpoints /api/face/check and /api/face/enroll; Cloudflare Tunnel
  for HTTPS on phones
- Module 3, Part 3: faculty/admin reset of a student's face registration (reason required);
  account history table (face resets, issued passwords, deactivation) with migration 0002
- Module 4, Part 1: session service (start, stop, auto-close, absentees filled in on close);
  `flask close-expired-sessions`; college-time dates (tzdata)
- Module 4, Part 2: rotating signed QR codes (new code every 10 s); faculty attendance
  screen with live counts, latest check-ins, full screen, Stop; start button on course page
- Module 4, Part 3: student check-in at /a/<code> (works from the phone camera app), in-app
  scanner page, "attendance is open" on the student home, scan log, optional network rule
- Module 4, Part 4: face check after the scan and marking present (once per session, 3 face
  attempts per check-in, safe under double taps and Stop at the same moment); shared camera.js
- Module 5, Part 1: manual attendance service (change any record with a reason, attendance
  for a date with no live session, audit history, session lists and rosters)
- Liveness (added before Module 5, Part 2): random head-turn challenge in the attendance face
  check, so a photo or phone screen of a student cannot be used; migration 0003

Next
- Module 5, Part 2: faculty pages for results, editing and past dates
- Module 5, Part 3: the same through the admin API, Postman update
- Modules 6 to 7: auth, admin API, face enrolment, attendance sessions, faculty view and manual edits, reports, hardening


## Requirements

- Windows 10/11
- Python 3.11 or 3.12 (check with `python --version`)
- PostgreSQL with pgAdmin


## First-time setup

1. Create the database user and databases. In pgAdmin, open the Query Tool on the `postgres` database and run:

```sql
CREATE ROLE attendance_app WITH LOGIN PASSWORD 'choose-a-strong-password';
CREATE DATABASE attendance_dev OWNER attendance_app;
CREATE DATABASE attendance_test OWNER attendance_app;
```

2. Create a virtual environment and install packages. In PowerShell, inside the project folder:

```powershell
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

If `activate` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

3. Create your `.env` file:

```powershell
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the printed value into `SECRET_KEY`, and put your database password into
`DATABASE_URL` and `TEST_DATABASE_URL`. If the password contains `@ : / # %`,
URL-encode it (for example `@` becomes `%40`).

4. Build the database tables:

```powershell
flask --app wsgi db upgrade
```

5. Create the admin account (prints an API token once; save it in Postman):

```powershell
flask --app wsgi create-admin --username admin --name "College Admin"
```

Lost the token? Issue a new one (the old one stops working):

```powershell
flask --app wsgi create-admin --username admin --name "College Admin" --rotate-token
```

6. Download Bootstrap (served from your own server, so pages work even if the
internet is slow or blocked in class). In Git Bash, from the project folder:

```bash
curl -L -o app/static/vendor/bootstrap/bootstrap.min.css https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css
curl -L -o app/static/vendor/bootstrap/bootstrap.bundle.min.js https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js
mkdir -p app/static/vendor/html5-qrcode
curl -L -o app/static/vendor/html5-qrcode/html5-qrcode.min.js https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js
sha256sum app/static/vendor/html5-qrcode/html5-qrcode.min.js
```

The last command should print
`660b12437b1d747e3e68b8be0685c08cb728140110ad213f167b14b66f8b1d8e` (the official 2.3.8 file).
If it does not, delete the file and download it again.

7. Install the new packages (whenever requirements.txt changes) and download the face models (about 39 MB, checked against
OpenCV's official checksums):

```bash
pip install -r requirements.txt
flask --app wsgi download-face-models
```

8. Optional, development only: add demo data (3 sections, 2 faculty, 10 students,
3 courses). All demo users have the password `Demo@12345` and must change it on
first login. Safe to run more than once.

```powershell
flask --app wsgi seed-demo
```


## Running

Tests (uses `attendance_test`, which is wiped on every run):

```powershell
pytest -v
```

Expected after the liveness part: 704 passed (15 of them need the face models;
without the models they show as skipped).

App (development):

```powershell
flask --app wsgi run --debug
```

Then open http://127.0.0.1:5000/health. You should see `{"database": "ok", "status": "ok"}`.
Open http://127.0.0.1:5000/ to log in (for example as `demo-fac01` / `Demo@12345`).


## Changing the database schema

Never edit tables in pgAdmin. Instead:

1. Change the model in `app/models/`.
2. `flask --app wsgi db migrate -m "short description"` creates a new file in `migrations/versions/`.
3. Open that file and check it. Alembic cannot detect triggers or changes to check
   constraints; add those by hand with `op.execute(...)`.
4. `flask --app wsgi db upgrade` applies it.
5. Run `pytest`. `test_migrated_schema_matches_models` fails if a model and the migrations disagree.

Other useful commands: `flask --app wsgi db current` (which revision the database is at),
`flask --app wsgi db downgrade` (undo the last migration).


## Project structure (current)

```
attendance-system/
├── .env.example          template for .env (copy and fill in)
├── .gitignore
├── pytest.ini
├── requirements.txt
├── wsgi.py               entry point for flask and waitress
├── README.md
├── models/               face model files (downloaded, not in git)
├── migrations/
│   ├── env.py            Alembic setup (uses the app's database settings)
│   └── versions/
│       ├── 0001_initial_schema.py   all tables + append-only audit trigger
│       ├── 0002_account_events.py   account history table + append-only trigger
│       └── 0003_liveness_challenge.py   head-turn order stored on each check-in
├── app/
│   ├── __init__.py       app factory: config checks, extensions, CLI, /health
│   ├── cli.py            flask create-admin, seed-demo, download-face-models, close-expired-sessions
│   ├── config.py         Development / Testing / Production settings
│   ├── extensions.py     db, migrate, login_manager, csrf, limiter
│   ├── blueprints/       routes, one folder per area (thin: read input, call service, respond)
│   │   ├── admin_api/    /api/admin/... JSON API for Postman (users.py, academic.py)
│   │   ├── auth/         /login, /logout (POST only), /change-password
│   │   ├── main/         / (sends each user to their own home page)
│   │   ├── student/      /student/ home (open attendance, scan button), /student/scan scanner
│   │   ├── attend/       /a/<code> check-in page (routes.py); /api/attendance face check (api.py)
│   │   ├── faculty/      dashboard, course roster, passwords, face reset (routes.py);
│   │   │                 start/live screen/stop (sessions.py); /api/faculty live JSON (api.py)
│   │   └── face/         /face/enroll page (routes.py) and /api/face/check|enroll (api.py)
│   ├── templates/        Jinja pages: base.html, auth/, student/, faculty/, face/, errors/
│   ├── static/
│   │   ├── css/app.css   colours and layout on top of Bootstrap
│   │   ├── js/page-freshness.js  reloads logged-in pages restored by the Back button
│   │   ├── js/confirm-forms.js   asks before submitting forms marked data-confirm
│   │   ├── js/api.js             postJSON(): sends CSRF token, never throws, readable errors
│   │   ├── js/camera.js          shared front-camera helpers (start, stop, take a photo, errors)
│   │   ├── js/face-enroll.js     guided registration photos, live checks
│   │   ├── js/attend-face.js     face check with head-turn prompts: countdown, attempts, result
│   │   ├── js/faculty-session.js live QR screen: polling, countdown, offline handling
│   │   ├── js/student-scan.js    in-app QR scanner; opens only this site's /a/<code> links
│   │   └── vendor/       bootstrap/ and html5-qrcode/ (downloaded, setup step 6)
│   ├── schemas/          input validation (shape of data) with marshmallow
│   │   ├── common.py     strict schemas, username/email/code fields, load_or_fail()
│   │   ├── user_schemas.py  create/update/list/output shapes for users
│   │   ├── academic_schemas.py  sections, courses, assignment, enrolment, roster
│   │   └── face_schemas.py   photo payloads (data URLs) for the face endpoints
│   ├── services/         business rules; no Flask request objects inside
│   │   ├── errors.py     ServiceError types (turned into HTTP errors by the routes)
│   │   ├── common.py     require_admin, valid_id, commit_or_conflict, escape_like
│   │   ├── academic_service.py  sections, courses, faculty assignment, enrolment
│   │   ├── face_engine.py    OpenCV models (YuNet detector, SFace recogniser); thread-safe
│   │   ├── face_service.py   photo decoding, quality checks, register, verify
│   │   ├── session_service.py  start/stop attendance, auto-close, absentees, counts
│   │   ├── qr_service.py     signed rotating codes, QR as SVG
│   │   ├── checkin_service.py  scan checks, pending check-in, scan log, network rule
│   │   ├── attendance_service.py  face check after the scan, marking present
│   │   ├── manual_service.py  manual changes with reasons, past dates, rosters, history
│   │   ├── auth_service.py   login, password rules, password change, admin token
│   │   ├── course_service.py course lists for the dashboards
│   │   └── user_service.py   create/edit/deactivate users, bulk create, temporary passwords
│   ├── models/
│   │   ├── __init__.py   imports every model in one place
│   │   ├── base.py       timestamp columns, string cleaning
│   │   ├── user.py       User, Role, Flask-Login user loader
│   │   ├── account.py    AccountEvent: who reset a face, issued a password, (de)activated
│   │   ├── academic.py   Section, Course, CourseFaculty, Enrollment
│   │   ├── attendance.py sessions, check-ins, records, audit trail, scan log
│   │   └── face.py       FaceEmbedding (numbers only, no photos)
│   └── utils/
│       ├── decorators.py role_required, admin_token_required (route protection)
│       ├── errors.py     turns errors into JSON (for /api/) or simple pages
│       ├── http.py       wants_json, safe redirect check for ?next=
│       ├── ratelimit.py  rate-limit keys (IP + username, per user)
│       ├── security.py   Argon2 password hashing, API-token hashing
│       ├── timeutils.py  utcnow(), local_date() in college time (Asia/Kolkata)
│       └── validators.py username format rules (shared by CLI and API)
└── tests/
    ├── conftest.py       builds the test database by running the real migrations
    ├── factories.py      helpers that create valid test rows
    ├── test_app_factory.py
    ├── test_security.py
    ├── test_models_user.py
    ├── test_models_academic.py
    ├── test_models_attendance.py
    ├── test_models_face.py
    ├── test_migrations.py    migrations match models, trigger exists, downgrade works
    ├── test_cli.py           create-admin and seed-demo
    ├── test_auth_service.py  login, password rules, password change, admin token
    ├── test_decorators.py    route protection, admin token, error responses
    ├── test_http_utils.py    safe redirects, rate-limit keys
    ├── test_auth_routes.py   login/logout/change-password pages, dashboards, every route protected
    ├── test_user_schemas.py  input validation for user management
    ├── test_user_service.py  user management rules and permissions
    ├── test_academic_service.py  sections, courses, assignment, enrolment, roster
    ├── test_admin_api.py     admin API over HTTP, token on every route
    ├── test_faculty_pages.py faculty roster page, issuing student passwords
    ├── face_helpers.py       synthetic photos and a stand-in face engine for tests
    ├── test_face_service.py  face rules with the stand-in engine (no models needed)
    ├── test_face_models.py   face rules with the REAL models (skipped until downloaded)
    ├── test_face_cli.py      download-face-models (network faked)
    ├── test_face_routes.py   face page and /api/face endpoints (stand-in engine)
    ├── test_face_reset.py    face reset (service, admin API, faculty page), account history
    ├── test_session_service.py  start/stop rules, races, auto-close, absentees, counts
    ├── test_qr_service.py    code signing, rotation, grace period, forgery, expiry
    ├── test_faculty_sessions.py  start/stop pages, live endpoint, permissions
    ├── test_checkin_service.py   scan checks, pending check-in, scan log, network rule
    ├── test_checkin_routes.py    /a/<code> page, scanner page, student home
    ├── test_attendance_service.py  face check, attempts, timing, marking present
    ├── test_attend_face_routes.py  face-check endpoint and a full class end to end
    ├── test_manual_service.py  manual changes, past dates, permissions, audit, rosters
    ├── liveness_helpers.py   builds head-turn checks for tests (live person, photo attack)
    └── fixtures/face_sample.jpg  public-domain NASA portrait used by the real-model tests
```

`postman/` holds the Postman collection and a local environment (see "Using the admin API").
(The originally planned `scripts/` folder became the `flask` commands in `app/cli.py`.)


## Key design rules

- Rules that protect data are enforced by PostgreSQL itself (check constraints,
  unique constraints, foreign keys), not only by Python code. They hold even for
  rows inserted from pgAdmin.
- Only faculty can be assigned to teach and only students can be enrolled; the
  database checks the user's role on every link.
- Nothing is deleted silently. Deleting a course, section or user that has linked
  rows is refused. Retire records with `is_active = False` instead.
- The same course code can exist in different terms (CS301 in 2026-ODD and 2027-ODD are separate courses).
- Changing a password bumps `session_version`, which logs the user out on every device.
- A student can be marked only once per session (unique constraint on session + student).
- Only one open attendance session per course at a time (partial unique index).
- Every attendance row records how it was made: `qr_face` (with face-match evidence),
  `manual` (with who marked it) or `auto_absent` (filled in when the session closes).
- The audit table is append-only: a PostgreSQL trigger blocks UPDATE and DELETE on it,
  even from pgAdmin. Every audit row needs a reason of at least 3 characters.
- `session_date` stores the class date in college time (IST); all timestamps are UTC.
- Faces are stored as embeddings (numbers) with the model name; no photos are kept.
- Tests refuse to run unless the test database name ends in `_test`.
- The test database is built from the migrations, so a broken migration fails the tests.
- Admins have no usable password; they work through Postman with an API token.
  Only a hash of the token is stored.
- The app refuses to start if DATABASE_URL points to a `_test` database, and tests
  refuse to run if TEST_DATABASE_URL and DATABASE_URL are the same database.


## Authentication rules

- Login failures always show the same message ("Invalid username or password"),
  whether the username is unknown, the password is wrong or the account is disabled.
- Unknown usernames still run a full password check, so response time does not
  reveal which usernames exist.
- Admins cannot log in with a password.
- Passwords: 8 to 128 characters, at least one letter and one number, no leading or
  trailing space, must not contain the username, must not be a common password.
- Changing a password requires the current password, ends every other login
  (session_version + 1), and completes the first-login step (must_change_password = False).
- Admin API requests use the header `Authorization: Bearer <token>`.
- Logout deletes the "keep me logged in" cookie as well as the session. (Fixed bug: the
  session used to be cleared after Flask-Login's logout, which erased its instruction to
  delete that cookie, so the next request logged the user straight back in.)
- After logout, the Back button must not show the previous page. Three layers handle this:
  logged-in pages are sent with `Cache-Control: no-store`; logout sends
  `Clear-Site-Data: "cache"`; and `static/js/page-freshness.js` reloads any logged-in page
  the browser restores from its back-forward cache, which then redirects to login.
- There is no account lockout after failed logins: a classmate could lock someone out
  just before class. Repeated attempts are slowed by rate limits instead: 10 per 5 minutes
  for one username from one IP, and 300 per 5 minutes per IP (a whole classroom shares
  one college IP).


## Face checks

Every photo goes through the same steps: decode safely (size limits, phone rotation fixed,
scaled to at most 1280 px), find exactly one clear face (much smaller faces in the
background are ignored), check quality, then turn the face into 128 numbers. Photos are
never stored; only the 128 numbers (averaged over 3 registration photos) are kept, with
the name of the model that produced them.

Thresholds live in `app/config.py` (`FACE_*`) and were calibrated on the official models:

- `FACE_MATCH_THRESHOLD = 0.363`: OpenCV's published value for SFace. Same person scored
  0.72 to 0.99 in calibration (different lighting, framing, compression); unrelated images
  about 0.15 to 0.18.
- `FACE_MIN_SHARPNESS = 25`: measured on the face resized to 112 px, so distance from the
  camera does not change it. Sharp photos score about 250 to 330; a clearly blurred one 15.
- `FACE_BRIGHTNESS_RANGE = (40, 225)`, `FACE_MIN_SIZE_PX = 80`, `FACE_ENROL_CAPTURES = 3`.

Registration rules: students register their own face, no approval needed. All 3 photos
must pass the checks and look like the same person; the exact same file twice is rejected.
A registered face cannot be replaced by the student (so nobody can quietly swap in their
own face on a borrowed phone).

Resetting a registration: the admin can reset any student (`POST /api/admin/users/<id>/face/reset`
with a `reason`); a faculty member can reset students actively enrolled in a course they
teach (course page, "Actions", "Reset face", with a reason). The registration is deleted and
the student is sent to register again on their next page. They are not logged out. If the face model is ever changed, old registrations are detected and
the student is asked to register again.

If the model files are missing, the app still runs; face features answer
"Face checks are not available right now" (HTTP 503) until
`flask --app wsgi download-face-models` is run.


## Opening the app on phones (HTTPS with Cloudflare Tunnel)

Phones only allow the camera on `https://` pages. Cloudflare Tunnel gives your laptop a
temporary public HTTPS address, with nothing to install on the phones.

1. Install cloudflared once (PowerShell or Git Bash):
   `winget install --id Cloudflare.cloudflared`
   (or download `cloudflared-windows-amd64.exe` from github.com/cloudflare/cloudflared/releases
   and rename it to `cloudflared.exe`). Close and reopen the terminal afterwards.
2. In `.env`, set `TRUSTED_PROXY_COUNT=1`, then start the app as usual:
   `flask --app wsgi run`
   Keep the default address (127.0.0.1). Do not use `--host 0.0.0.0` while the tunnel is in
   use: then only the tunnel can reach Flask, so nobody can fake their IP address.
3. In a second terminal: `cloudflared tunnel --url http://localhost:5000`
4. It prints an address like `https://random-words.trycloudflare.com`. Open it on the phone.
   The address changes every time cloudflared restarts.

Why `TRUSTED_PROXY_COUNT=1`: every request arrives through cloudflared, so without it the
app thinks all students share one IP address (127.0.0.1), which breaks the per-IP login
limit and logs. Set it back to 0 when not using the tunnel.

Anyone with the trycloudflare.com link can reach the login page while the tunnel runs.
That is fine for development and demos; stop cloudflared (Ctrl+C) when finished.


## Face registration page

After the first password change, students land on `/face/enroll`:

1. "Start camera" asks for camera permission (front camera, preview shown like a mirror).
2. The student takes 3 photos with short prompts (look straight, tilt slightly, look
   straight). Each photo is checked by the server right away (`POST /api/face/check`),
   so problems like "too dark" or "move closer" are fixed before the next photo.
3. After the third photo, all 3 go to `POST /api/face/enroll`; the server checks every
   photo again and saves only the face description. The student is sent to their home page.

The page explains blocked camera permission, a busy camera, a non-HTTPS address, lost
connection and an expired login, and pauses the camera when the student switches app
(photos already taken are kept). Photos are sent at most 960 px wide as JPEG.
Limits: 40 photo checks per 5 minutes and 10 registration attempts per 10 minutes per student.


## Account history

`account_events` records who did what to an account, and when: face resets (with the
required reason), temporary passwords issued, deactivation and reactivation. Like the
attendance audit, a PostgreSQL trigger makes it append-only: rows cannot be changed or
deleted, even from pgAdmin. The admin reads it with `GET /api/admin/users/<id>/history`
(newest first). Passwords themselves are never recorded.


## Attendance sessions

- Only a faculty member assigned to the course starts or stops its attendance; a co-teacher
  can stop a session the other started.
- One open session per course. Pressing Start again, or a co-teacher pressing it, returns
  the session already open. Two simultaneous clicks still create only one (the database
  decides; tested with real parallel requests).
- One running session per faculty member at a time, and a course needs at least one
  enrolled, active student.
- Two lectures on the same day are two separate sessions.
- Each session gets its own random secret (used to sign its QR codes in Part 2).
- `session_date` is the class date in college time; all timestamps are stored in UTC.
- Sessions close themselves 120 minutes after starting (`SESSION_MAX_MINUTES`), and are
  recorded as ending at that time. No background job is needed: overdue sessions are closed
  whenever any session action runs. For a scheduled clean-up, run
  `flask --app wsgi close-expired-sessions` (safe to run any time).
- On closing, every actively enrolled student with an active account and no record is marked
  absent (`auto_absent`), unfinished check-ins are discarded, and totals are fixed: students
  enrolled later never appear as absent in older sessions. A student marked present stays
  present even if dropped later. Stopping twice is harmless.


## Rotating QR codes

A code looks like `<session>.<window>.<signature>`. The window changes every 10 seconds
(`QR_ROTATION_SECONDS`), so the code on screen changes every 10 seconds. The signature is
an HMAC-SHA256 keyed by the session's own random secret mixed with `SECRET_KEY`: codes
cannot be forged, a code for one session never works for another, and a copy of the
database alone is not enough to make codes. A code is accepted in its own window and one
window after (`QR_GRACE_WINDOWS = 1`), so it lives 10 to 20 seconds and a scan just as the
code changes still counts. Nothing is stored per code; the server recomputes the signature.

The QR holds a full link, `https://<site>/a/<code>`, built from the address the faculty
opened the screen with. That is why the faculty must open the attendance screen through
the Cloudflare Tunnel address: on `localhost` the page shows a warning, because phones
cannot open that link.

## Faculty attendance screen

Course page, "Start attendance" opens the live screen: a large QR code (SVG, sharp on a
projector), a bar counting down to the next code, counts (present / not yet / enrolled),
the latest check-ins, "Full screen" for projectors, and "Stop attendance".

- The screen asks the server every 2 s and right after each code change, using the
  server's timing (not the laptop clock).
- If the connection drops, a banner appears and retries slow down (up to every 5 s). If no
  fresh code arrives before the shown one expires, the code is blurred out, so students
  never scan a code that would be rejected.
- If a co-teacher stops attendance or it closes automatically, the screen switches to the
  final summary. The screen is kept awake while attendance runs (where the browser allows).


## Student check-in (scanning)

Students scan the code either with their phone's normal camera app (the QR holds a link,
so tapping it opens the check-in page) or with the in-app scanner ("Scan attendance code"
on their home page, which uses the back camera). The in-app scanner only ever opens this
site's `/a/<code>` page: a QR code pointing anywhere else, such as a fake code stuck over
the real one, is refused.

Checks, in order: the code is genuine and current and its attendance is still open; the
phone is on an allowed network (optional, below); the student is actively enrolled in the
course; their face is registered; they have no record in this session yet. If all pass, a
pending check-in is created that lasts 60 seconds (`PENDING_CHECKIN_SECONDS`); the face
check (Module 4, Part 4) must be finished within that time. Scanning again refreshes the
60 seconds but keeps the number of face attempts already used.

A logged-out student is sent to log in and then back to the link; by then the code has
usually changed, so the page says "Code expired, scan again". Students already marked
present get a friendly "Already marked present" page. Scans are limited to 30 per 5
minutes per student.

Every scan, accepted or not, is written to `scan_logs` with the outcome (`ok`,
`expired_code`, `invalid_code`, `session_closed`, `not_enrolled`, `face_not_registered`,
`outside_network`, `already_present`, `already_recorded`), the session when known, the IP
address and the browser. A forged code is never tied to a session in the log.

Optional network rule: set `ATTENDANCE_ALLOWED_NETWORKS` in `.env` to the college's public
IP addresses or ranges (comma-separated, e.g. `203.0.113.0/24,198.51.100.7`) to accept scans
only from the college network. This makes forwarding a code to someone at home harder.
Leave it empty to allow any network. Invalid entries stop the app at startup with a message.
Through Cloudflare Tunnel the address checked is the phone's public IP, so ask the college
IT team for the Wi-Fi's public address range. Students on mobile data will be refused.


## Face check and marking present

After a scan is accepted, the check-in page becomes the face check: "Start camera" (front
camera), "Check my face", and a 60-second countdown. One photo is compared with the
student's OWN registered face; on a match the student is marked present and sees
"You are marked present for DS301 ... at 10:42". The faculty screen count goes up within
about 2 seconds.

- Marked at most once per session: the database's unique rule plus row locks. Two taps at
  the same moment both answer "present" and only one record exists (tested with real
  parallel requests).
- Pressing Stop while a student's face is being checked: the student ends up with exactly
  one record, either present (the check finished first) or absent (Stop came first). No
  deadlock (tested in parallel; the session row is always locked before the check-in row).
- A friend holding their face up to your phone (logged in as you) fails: only your own
  registered face can match.
- 3 mismatches per check-in (`FACE_CHECK_ATTEMPTS`); rescanning the code does not reset the
  count. After that the student is told to ask the faculty, who can mark them manually
  (Module 5). A photo that cannot be checked (too dark, no face, ...) does not use an attempt.
- Finished after 60 seconds, after attendance stops, or for a student with no scan: refused
  with a clear message and a "Scan the code again" or "Go home" button.
- Each attempt is logged in `scan_logs` with `event = 'face'` and an outcome (`matched`,
  `face_mismatch`, `too_many_attempts`, `photo_<problem>`, `checkin_expired`, ...), with the
  similarity score. The present record keeps the face distance as evidence.
- Limit: 20 face photos per 5 minutes per student. Endpoint:
  `POST /api/attendance/sessions/<id>/face` with `{"image": "data:image/jpeg;base64,..."}`.


## Liveness: stopping photos of students

A single photo only proves whose face is in front of the camera, not that a live person is
there. So the attendance face check asks for a short random head turn:

1. At each scan the server picks a random order: "left then right" or "right then left".
2. On the phone, "Check my face" shows: look straight, then an arrow and "Turn your head
   slightly to your LEFT" (or RIGHT), then the other way. Frames are taken automatically
   (2 straight, 4 per turn, about 4-5 seconds in total).
3. The server checks: the straight frame matches the registered face (as before); the head
   really turned each way, in the order asked; and the turned frames are the same person.

How a turn is measured: the nose tip's position between the eyes (from the face detector's
landmarks), in eye-widths, compared with the same student's own "straight" frame. A real
slight 20 degree turn moves it about 0.12, because the nose sticks out and shifts towards
one eye. A flat photo or phone screen, however it is tilted, barely moves it, because a
flat picture has no depth. Measured with the real detector: the sample photo looking
"straight" was -0.073 (nobody faces a phone perfectly, which is why turns are measured
from each student's own straight pose); tilting that photo by 25-40 degrees moved it at
most about 0.065, and the full photo attack moved it 0.033 against the 0.12 needed.

What it stops: a photo or phone screen held up to the camera; turning the wrong way (a
pre-recorded video made for the other order); showing a photo for the straight frame and
then one's own head for the turns. A failed liveness check uses one of the 3 attempts, so
the trick cannot be retried forever. Not looking straight at the start, or unusable frames,
do not use an attempt. Each check is logged with its numbers in `scan_logs.detail`, e.g.
`LR sim=0.871 straight=+0.012 turn1L=+0.183 turn1sim=0.71/0.80 turn2R=+0.164 ...`.

Settings in `app/config.py`: `FACE_STRAIGHT_MAX_OFFSET = 0.15` (roughly facing the camera),
`FACE_TURN_MIN_OFFSET = 0.12` (movement from straight), `FACE_TURN_MATCH_THRESHOLD = 0.30`,
`FACE_YAW_SIGN = 1`.

Limits: a very determined student could still use a live video call of the absent friend
following the arrows, or a deepfake app. Students who cannot turn their head (injury) are
marked manually by the faculty.

Not covered: two students agreeing that one registers their face on the other's account.
Liveness cannot detect that (the face is real); faculty supervision of first registration
is the defence.


## Manual attendance and corrections

Who: the admin, or a faculty member assigned to the course (co-teachers included).

- Any change needs a reason (3-500 characters). Each change writes an audit row: old
  status, new status, reason, who, when. The audit table is append-only, so the history
  cannot be edited or deleted, even from pgAdmin.
- A changed record becomes method "manual" with who and when; the original face-check
  evidence is kept. Several students can be changed at once with one reason, all or
  nothing; students already at that status are reported as unchanged (no audit row).
- During a live class: a student who failed the face check 3 times can be marked present;
  marking a student absent stops them checking in later.
- A record can be corrected even if the student was dropped or deactivated since. A new
  record can only be added for a student actively enrolled with an active account.
- Any date: for a date without a live session, a closed "manual" session is created and
  every actively enrolled student gets a record (ticked = present, everyone else absent).
  Future dates are refused (judged in college time). If the course already has attendance
  on that date, the faculty must confirm it is a second class that day.

Concurrency: all services lock rows in the same order (session, then pending check-in,
then record). A real parallel test of a manual change and a face check finishing at the
same moment found a deadlock in the first version; with the fixed order, every round ends
with exactly one record and no errors.


## Using the admin API (Postman)

1. Start the app: `flask --app wsgi run`.
2. In Postman, Import both files from `postman/`:
   `qr-attendance-admin.postman_collection.json` and `qr-attendance-local.postman_environment.json`.
3. Open the environment "QR Attendance - Local" and paste your admin token into `admin_token`
   (the token printed by `flask --app wsgi create-admin`; lost it? use `--rotate-token`).
4. Select that environment (top right) and run the requests top to bottom the first time.
   Requests that create something save its id for the requests after them.

Running the Create requests again returns 409 (already exists); change the codes or
usernames in the body to create new records.

Endpoints (all under `/api/admin`, all need `Authorization: Bearer <token>`):

- People: `POST /faculty`, `POST /students`, `POST /students/bulk`, `GET /users`,
  `GET|PATCH /users/<id>`, `POST /users/<id>/deactivate|activate|temporary-password`,
  `POST /users/<id>/face/reset` (body `reason`), `GET /users/<id>/history`
- Sections: `GET|POST /sections`, `PATCH /sections/<id>`, `POST /sections/<id>/retire|restore`
- Courses: `GET|POST /courses`, `GET|PATCH /courses/<id>`, `POST /courses/<id>/retire|restore`
- Teaching: `POST /courses/<id>/faculty` (body `username`), `DELETE /courses/<id>/faculty/<username>`
- Enrolment: `POST /courses/<id>/enrolments` (body `usernames`),
  `POST /courses/<id>/enrolments/section` (body `section_code`),
  `DELETE /courses/<id>/enrolments/<username>`, `GET /courses/<id>/roster?include_dropped=true`

Responses: single records are wrapped (`{"user": ...}`, `{"course": ...}`); lists are
`{"items": [...], "total": n, "page": p, "per_page": n}`; errors are
`{"error": code, "message": ..., "details": {field: [messages]}}` with 401 (token),
404 (not found), 409 (already exists) or 422 (invalid input). API responses are never cached.

Faculty can also issue a new temporary password to a student from the website: open the
course from the faculty home page and press "New password" next to the student. The
password is shown once on screen and is never stored in the session cookie.


## User management rules

- Schemas check the shape of input (types, lengths, formats). Unknown fields are rejected,
  so a typo like `fullname` is reported instead of silently ignored. Services check the
  rules (section exists, username free, who may do what).
- Only the admin creates, edits, lists and deactivates faculty and students.
- Usernames (roll numbers / staff IDs) and roles cannot be changed after creation: the
  roll number is the student's identity in every attendance record.
- Bulk student creation is all or nothing (up to 500 per request). If any row is wrong,
  nothing is saved and every problem is listed by row number.
- Temporary passwords look like `k7mp-4qrx-9tdw`: no look-alike characters (0/O, 1/l), easy
  to read out or write down. They are shown once, stored only as a hash, and force a
  password change at next login. Issuing one signs the user out everywhere.
- The admin can issue temporary passwords to any faculty or student. A faculty member can
  issue them only to students actively enrolled in an active course they teach.
- Deactivating an account signs it out immediately and blocks login. Nothing is deleted.
- Admin accounts are invisible to the API; they are managed only with `flask create-admin`.


## Academic data rules

- Only the admin changes sections, courses, faculty assignments and enrolments. A faculty
  member can view the roster of a course they teach.
- Codes (sections, courses, terms) are stored uppercase and may contain letters, digits,
  `-` and `_`. A course code can repeat in different terms (DS301 in 2026-ODD and 2027-ODD).
  Codes can be corrected later; records point to ids, so history is not affected.
- Nothing is deleted. Sections and courses are retired (`is_active = False`). A retired
  course is read-only and disappears from dashboards; restoring it brings everything back.
- Enrolling by username list is all or nothing: if any username is unknown, not a student,
  or deactivated, nobody is enrolled and every problem is listed. Repeated usernames are
  ignored. Already-enrolled students are reported, not treated as errors.
- Enrolling a whole section adds all its active students in one step (the usual way
  colleges enrol a class). Deactivated students are skipped.
- Dropping a student keeps the enrolment row (inactive) for history. Enrolling them again
  reuses the same row.
- Assigning the same faculty twice is harmless. Unassigning a faculty member also removes
  their right to issue passwords to that course's students.


## Route protection

Every route uses exactly one decorator:

- `@role_required(Role.STUDENT)` / `@role_required(Role.FACULTY)` for pages and APIs.
- `@admin_token_required` for the admin API (Postman). Login cookies are ignored there.

`role_required` checks, in order: logged in, password change done, correct role, and
(students only) face enrolled. Each failure sends the user to the page that fixes it,
or returns JSON for `/api/` routes. The change-password page uses
`allow_pending_password=True`; the face-enrolment page uses `allow_without_face=True`.

A test (`test_every_route_declares_who_may_use_it`) fails if any route is added without
one of these decorators. Only `/login`, `/health` and static files are public.

All JSON endpoints live under `/api/`. Errors there always look like
`{"error": "<code>", "message": "...", "details": {...}}`. Internal error text is never
sent to the browser; it goes to the server log.


## Troubleshooting

- `ModuleNotFoundError: No module named 'app'`: run commands from the folder that contains `wsgi.py`.
- Database connection fails: check the port in pgAdmin (Server, Properties, Connection); it may be 5433 instead of 5432.
- `SECRET_KEY is not set` or `DATABASE_URL is not set`: your `.env` file is missing or in the wrong folder; it must sit next to `wsgi.py`.
- `relation "..." already exists` during `db upgrade`: the tables were created earlier without migrations.
  In pgAdmin, open the Query Tool on `attendance_dev` and run BOTH lines together (select all, then Execute):
  `DROP SCHEMA public CASCADE;` and `CREATE SCHEMA public AUTHORIZATION attendance_app;`. Then upgrade again.
- `no schema has been selected to create in` during `db upgrade`: the app's user cannot use the
  `public` schema (it is missing, or it was recreated by `postgres` without permissions). In pgAdmin,
  Query Tool on `attendance_dev`, run:
  `CREATE SCHEMA IF NOT EXISTS public;` and `ALTER SCHEMA public OWNER TO attendance_app;`
  then upgrade again.
- Using Git Bash instead of PowerShell: activate with `source venv/Scripts/activate`; all other commands are the same.
- `Error: No such command 'db'`: the virtual environment is not active (`venv\Scripts\activate`).
- Back button still shows a page after logout: make sure `app/static/js/page-freshness.js`
  exists, then hard-refresh once (Ctrl+F5) so the browser drops the old cached copy.
- Camera page says it needs a secure (https://) page: open the trycloudflare.com address,
  not http://127.0.0.1 or your laptop's IP, on the phone.
- Phone shows "Camera permission was blocked": tap the lock icon next to the address,
  open Permissions, allow Camera, reload the page.
- trycloudflare.com page does not load: check both terminals are running (Flask and
  cloudflared) and that cloudflared printed the address without errors.
- Every student hits "Too many login attempts" together: `TRUSTED_PROXY_COUNT` is 0 while
  using the tunnel; set it to 1 and restart Flask.
- Attendance screen warns that phones cannot open the address: you opened it on
  http://127.0.0.1 or localhost. Open it through the trycloudflare.com address instead.
- QR code blurred with "Code out of date": the laptop lost its connection to the server
  (or the tunnel stopped). Check the Wi-Fi and that both terminals are running.
- Students keep getting "We could not see your head turn": look at `scan_logs.detail` for
  their face checks. The `turn1`/`turn2` values are movement from their straight pose. If
  they are small (0.05-0.11), they turn too little: tell them to turn a bit more, or lower
  `FACE_TURN_MIN_OFFSET` slightly (not below 0.09: tilted photos moved up to ~0.065).
  If the values are large but negative where the arrow asked for positive (and the
  other way round) for everyone, set `FACE_YAW_SIGN = -1`.
- Student says "Your face did not match" in normal light: check their registration was
  good (faculty course page shows "Ready"); if needed reset the face and let them register
  again in good light. Repeated mismatches show in `scan_logs` with the similarity score.
- Scanner page says "The scanner is not available": `html5-qrcode.min.js` is missing from
  `app/static/vendor/html5-qrcode/` (setup step 6). Students can still use the camera app.
- Every scan says "Connect to the college Wi-Fi": `ATTENDANCE_ALLOWED_NETWORKS` does not
  include the network's public IP. Check the scan log for the IPs seen, or leave it empty.
- `ZoneInfoNotFoundError: 'No time zone found with key Asia/Kolkata'`: the `tzdata` package
  is missing (Windows has no built-in timezone data). Run `pip install -r requirements.txt`.
- `download-face-models` fails: check the internet connection and run it again; a
  failed or altered download is discarded, never half-saved. Behind a college proxy, set
  `HTTPS_PROXY` in the terminal first.
- Face checks answer "not available right now": the model files are missing or damaged;
  run `flask --app wsgi download-face-models`.
- Pages look unstyled (plain text, no colours): Bootstrap was not downloaded. Run the two
  curl commands in setup step 6, then hard-refresh the browser (Ctrl+F5).
- "This form expired or came from another site": the page was open across a server restart
  with a changed SECRET_KEY, or cookies are blocked. Reload the page and submit again.