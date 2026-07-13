## Notes

- Fixture is a tiny Flask app in `app.py`.
- Two obvious planted issues are already documented in the source: SQL injection on `/login` and IDOR on `/account/<user_id>`.
- No other code paths exist in this fixture.
- Review status: no additional attack surfaces or vulnerabilities found beyond the two documented routes.
