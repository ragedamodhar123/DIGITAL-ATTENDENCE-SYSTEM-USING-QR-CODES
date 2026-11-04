# Digital Attendance System

Current stage: Module 1, Part 1.1 — folder structure and database connection.

## Run it

1. Create the database user and databases (once):

       psql -U postgres -f scripts/setup_postgres.sql
   or, with Docker:

       docker compose up -d

2. Set up Python:

       python -m venv venv
       venv\Scripts\activate          (Windows)   |   source venv/bin/activate (macOS/Linux)
       pip install -r requirements.txt
       copy .env.example .env         (Windows)   |   cp .env.example .env

3. Check the connection:

       flask check-db
       flask run --debug
   Then open http://127.0.0.1:5000/health in a browser or Postman.

4. Run the tests:

       pytest
