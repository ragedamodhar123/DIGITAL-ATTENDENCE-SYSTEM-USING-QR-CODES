-- Run once as the postgres superuser (native install, not Docker):
--   psql -U postgres -f scripts/setup_postgres.sql
CREATE ROLE attendance_app LOGIN PASSWORD '7a9a9a5a';
CREATE DATABASE attendance_dev OWNER attendance_app;
CREATE DATABASE attendance_test OWNER attendance_app;
