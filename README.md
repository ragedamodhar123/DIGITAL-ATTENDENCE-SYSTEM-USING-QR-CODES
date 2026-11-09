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

Next
- Module 3: face registration (camera capture, face embedding, quality checks)
- Modules 4 to 7: auth, admin API, face enrolment, attendance sessions, faculty view and manual edits, reports, hardening


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
```

7. Optional, development only: add demo data (3 sections, 2 faculty, 10 students,
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

Expected after Module 2, Part 3: 418 passed.

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
├── migrations/
│   ├── env.py            Alembic setup (uses the app's database settings)
│   └── versions/
│       └── 0001_initial_schema.py   all tables + append-only audit trigger
├── app/
│   ├── __init__.py       app factory: config checks, extensions, CLI, /health
│   ├── cli.py            flask create-admin, flask seed-demo
│   ├── config.py         Development / Testing / Production settings
│   ├── extensions.py     db, migrate, login_manager, csrf, limiter
│   ├── blueprints/       routes, one folder per area (thin: read input, call service, respond)
│   │   ├── admin_api/    /api/admin/... JSON API for Postman (users.py, academic.py)
│   │   ├── auth/         /login, /logout (POST only), /change-password
│   │   ├── main/         / (sends each user to their own home page)
│   │   ├── student/      /student/ dashboard
│   │   ├── faculty/      /faculty/ dashboard, /faculty/courses/<id> roster, issue student passwords
│   │   └── face/         /face/enroll (placeholder until Module 3)
│   ├── templates/        Jinja pages: base.html, auth/, student/, faculty/, face/, errors/
│   ├── static/
│   │   ├── css/app.css   colours and layout on top of Bootstrap
│   │   ├── js/page-freshness.js  reloads logged-in pages restored by the Back button
│   │   ├── js/confirm-forms.js   asks before submitting forms marked data-confirm
│   │   └── vendor/bootstrap/  bootstrap.min.css, bootstrap.bundle.min.js (downloaded, step 6)
│   ├── schemas/          input validation (shape of data) with marshmallow
│   │   ├── common.py     strict schemas, username/email/code fields, load_or_fail()
│   │   ├── user_schemas.py  create/update/list/output shapes for users
│   │   └── academic_schemas.py  sections, courses, assignment, enrolment, roster
│   ├── services/         business rules; no Flask request objects inside
│   │   ├── errors.py     ServiceError types (turned into HTTP errors by the routes)
│   │   ├── common.py     require_admin, valid_id, commit_or_conflict, escape_like
│   │   ├── academic_service.py  sections, courses, faculty assignment, enrolment
│   │   ├── auth_service.py   login, password rules, password change, admin token
│   │   ├── course_service.py course lists for the dashboards
│   │   └── user_service.py   create/edit/deactivate users, bulk create, temporary passwords
│   ├── models/
│   │   ├── __init__.py   imports every model in one place
│   │   ├── base.py       timestamp columns, string cleaning
│   │   ├── user.py       User, Role, Flask-Login user loader
│   │   ├── academic.py   Section, Course, CourseFaculty, Enrollment
│   │   ├── attendance.py sessions, check-ins, records, audit trail, scan log
│   │   └── face.py       FaceEmbedding (numbers only, no photos)
│   └── utils/
│       ├── decorators.py role_required, admin_token_required (route protection)
│       ├── errors.py     turns errors into JSON (for /api/) or simple pages
│       ├── http.py       wants_json, safe redirect check for ?next=
│       ├── ratelimit.py  rate-limit keys (IP + username, per user)
│       ├── security.py   Argon2 password hashing, API-token hashing
│       ├── timeutils.py  utcnow() (always timezone-aware)
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
    └── test_faculty_pages.py faculty roster page, issuing student passwords
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
  `GET|PATCH /users/<id>`, `POST /users/<id>/deactivate|activate|temporary-password`
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
- Pages look unstyled (plain text, no colours): Bootstrap was not downloaded. Run the two
  curl commands in setup step 6, then hard-refresh the browser (Ctrl+F5).
- "This form expired or came from another site": the page was open across a server restart
  with a changed SECRET_KEY, or cookies are blocked. Reload the page and submit again.