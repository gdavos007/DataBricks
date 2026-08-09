This pull request redacts a hard-coded Lakebase (Postgres) connection string from thunderhawk-weather-rag-app/README.md and replaces it with instructions to store the DB URL in Databricks secrets or environment variables.

Why:
- A full Postgres connection URL (including username and password) was present in the README and exposed credentials in the repository history.
- The change removes the literal credential from the README, replaces it with a redacted placeholder, and instructs users to use Databricks secrets.

Actions performed in this branch:
- Replaced thunderhawk-weather-rag-app/README.md with a redacted version that no longer includes the literal DB URI.

Recommended follow-ups after merging:
1. Rotate the leaked DB credentials immediately.
2. Purge the secret from git history (BFG or git-filter-repo) and force-push the cleaned history.
3. Verify there are no other occurrences of the sensitive host/username/password elsewhere in the repo or forks.
4. Add secret scanning and pre-commit hooks to prevent future leaks.

Signed-off-by: Copilot
