# Admin checklist: start of term, during term, end of term

All admin work uses the Postman collection (`postman/`) with the admin token. See the
README section "Using the admin API (Postman)".

## Start of term

1. Server ready: `flask --app wsgi check` shows all OK. Backups scheduled; one restore
   practised (README, Backups).
2. Sections: create every section (folder "1. Sections").
3. Faculty: create each faculty member ("Create faculty"). Give each their temporary
   password in person or by a private message; never in a group.
4. Students: "Bulk create students", up to 500 per request, one request per section is
   easiest. If any row is wrong, nothing is created and every problem is listed; fix and
   send again. Save the returned temporary passwords somewhere private and hand them out
   in class.
5. Courses: create each course with this term's code ("Create course"), assign its
   faculty ("Assign faculty") and enrol whole sections ("Enrol whole section").
6. Check: log in as one faculty member; their courses and student counts should be right.
7. Supervised first login: in each section's first class, students set their password and
   register their face while the faculty watches. This is the main protection against one
   student registering a friend's face on their account.
8. Trial class: one faculty member runs attendance in a real classroom with the full
   section. Check the count reaches everyone present, then stop and download a report.

## During term

- New student: "Create student", then enrol them in their courses.
- Forgotten password: faculty can issue one from the course page; you can use "Issue
  temporary password".
- Face keeps failing for a student: "Reset face registration" (with a reason), then the
  student registers again in good light.
- Student leaves: "Deactivate user". Nothing is deleted; their history stays.
- Weekly: check `logs\backup.log` and copy the `backups` folder off the machine. Glance at
  `logs\attendance.log` for repeated failed logins.
- Monthly: `pip-audit -r requirements.txt` for security updates.

## End of term

1. Download reports for every course (folder "5. Reports", or faculty download them).
2. Take a backup and keep a copy for the college's records.
3. Retire this term's courses ("Retire course"). Next term, create new courses with the
   new term code; old attendance stays as it was.
4. Rotate the admin token: `flask --app wsgi create-admin --username admin --name "College Admin" --rotate-token`.