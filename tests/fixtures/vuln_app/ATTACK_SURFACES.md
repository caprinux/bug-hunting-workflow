## Attack Surfaces

- `GET /login`
  - Takes `username` from query params.
  - Builds and executes a raw SQL statement.
  - Returns the executed query and matched row.
  - Primary risk: SQL injection due to string interpolation into the `WHERE` clause.

- `GET /account/<user_id>`
  - Returns user identity and SSN by numeric ID.
  - No authentication or authorization checks.
  - Primary risk: insecure direct object reference exposing sensitive PII.

## Assessment Notes

- Codebase is a single-file Flask fixture with no additional routes or helpers.
- No new attack surfaces were identified beyond the two obvious endpoints above.
