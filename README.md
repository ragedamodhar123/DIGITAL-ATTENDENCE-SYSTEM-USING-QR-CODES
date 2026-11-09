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

Next
- Module 1, Part 2: role decorators, first-login and face-enrolment gates, rate-limit keys
- Module 1, Part 3: login, logout and change-password pages
- Modules 2 to 7: auth, admin API, face enrolment, attendance sessions, faculty view and manual edits, reports, hardening


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

6. Optional, development only: add demo data (3 sections, 2 faculty, 10 students,
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

Expected after Module 1, Part 1: 202 passed.

App (development):

```powershell
flask --app wsgi run --debug
```

Then open http://127.0.0.1:5000/health. You should see `{"database": "ok", "status": "ok"}`.


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
│   ├── services/         business rules; no Flask request objects inside
│   │   ├── errors.py     ServiceError types (turned into HTTP errors by the routes)
│   │   └── auth_service.py  login, password rules, password change, admin token
│   ├── models/
│   │   ├── __init__.py   imports every model in one place
│   │   ├── base.py       timestamp columns, string cleaning
│   │   ├── user.py       User, Role, Flask-Login user loader
│   │   ├── academic.py   Section, Course, CourseFaculty, Enrollment
│   │   ├── attendance.py sessions, check-ins, records, audit trail, scan log
│   │   └── face.py       FaceEmbedding (numbers only, no photos)
│   └── utils/
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
    └── test_auth_service.py  login, password rules, password change, admin token
```

Folders such as `app/schemas/`, `app/blueprints/`,
`app/templates/`, `app/static/` and `postman/` are added by later modules.
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
- There is no account lockout after failed logins: a classmate could lock someone out
  just before class. Repeated attempts are slowed by rate limits instead (Module 1, Part 2).


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