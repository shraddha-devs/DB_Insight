# InsightSQL

InsightSQL is a local-first workbench for asking questions about the repository's employee-management SQLite database. It generates SQLite SQL through an optional OpenAI-compatible API, shows the SQL for review/editing, then runs it through layered read-only controls. SQL execution remains available without an AI key.

## Stack and rationale

- **FastAPI + Pydantic** provide typed request validation, centralized error handling, and an asynchronous web/API layer.
- **SQLite + Python's `sqlite3`** reuse the included database without adding a database server; the source database is opened with `mode=ro` and `query_only`.
- **SQLGlot** parses SQL into an AST for single-statement, SELECT-only validation before execution.
- **Vanilla HTML/CSS/JavaScript** keep the UI fast, small, and dependency-light for this four-table local application.
- **Docker Compose** runs the service as a non-root user with a read-only root filesystem and read-only source database mount.

## Requirements

- Python 3.11 or newer for local execution (the container uses Python 3.13).
- An existing `employee_management.db` (included in this repository).
- Optional: an API key for an OpenAI-compatible Chat Completions endpoint. Without it, manual SQL execution and query history still work.

The natural-language question and approved table/column names are sent to the configured AI provider when SQL generation is requested. Query results and database contents are never sent to the AI provider. Do not send sensitive questions unless permitted by your organization's data policy.

## Run locally

From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Open `.env` and set `OPENAI_API_KEY` to enable AI generation. It is also valid to leave the key empty and use the SQL editor directly. Then start the app:

```powershell
python -m uvicorn insightsql.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Query requests are restricted to one read-only `SELECT` on the four approved application tables.

Run the test suite:

```powershell
python -m pytest -q
```

## Docker

Copy `.env.example` to `.env`, set non-empty `INSIGHTSQL_USERNAME` and `INSIGHTSQL_PASSWORD`, and optionally configure the AI key. Then run:

```powershell
docker compose up --build
```

Open <http://127.0.0.1:8000>. Compose binds only to loopback, mounts `employee_management.db` read-only, enables HTTP Basic authentication, and stores capped query history in a named volume. Use a TLS-terminating reverse proxy and a proper identity provider before exposing this beyond the local machine; do not publish the container port directly to an untrusted network.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness check. |
| `GET` | `/api/schema` | Allowlisted table/column metadata. |
| `POST` | `/api/generate` | Generate and validate SQL from `{ "question": "..." }`. Requires `OPENAI_API_KEY`. |
| `POST` | `/api/query` | Validate and execute `{ "sql": "SELECT ...", "question": "..." }`. |
| `GET` | `/api/history` | Read the most recent 100 questions and SQL statements. Results are not stored. |
| `DELETE` | `/api/history` | Clear local query history. |

All errors use `application/problem+json` and carry a request ID. Interactive API docs are disabled by default.

## Read-only security boundary

Every query passes all of these controls; AI prompting is not a security control:

1. Request bounds limit question, SQL, and configuration values; a per-process IP rate limiter protects query/generation endpoints.
2. SQLGlot must parse exactly one statement whose root is `SELECT`. DML, DDL, PRAGMA, ATTACH, unknown tables, schema-qualified access, and unapproved functions are rejected. Table/column references are checked against the approved schema.
3. Missing `LIMIT` is added; requested row counts are clamped. OFFSET is bounded. Execution stops at the configured wall-clock/instruction budget; columns, SQLite values, and serialized result size are bounded.
4. The database opens using SQLite URI `mode=ro`, sets `PRAGMA query_only=ON`, and installs a SQLite authorizer that denies every action except approved reads/selects and approved pure functions.
5. Docker mounts the source database read-only and runs a non-root process in a read-only container filesystem.

The query history database is a separate writable file and stores the question, normalized SQL, row count, truncation flag, and timestamp, but not result data. Local history is capped at 100 records. The included SQLite file contains synthetic sample data; treat any replacement database as confidential and protect the host filesystem accordingly.

## Configuration

See [.env.example](.env.example). Important values:

- `INSIGHTSQL_DATABASE_PATH`: source database path. Defaults to the included employee database.
- `INSIGHTSQL_HISTORY_PATH`: separate local history database.
- `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_CHAT_COMPLETIONS_URL`: optional AI provider settings. The key must remain server-side.
- `INSIGHTSQL_MAX_ROWS`: row cap (1-1000; default 500).
- `INSIGHTSQL_QUERY_TIMEOUT_SECONDS`: query budget (0.1-10 seconds; default 2).
- `INSIGHTSQL_USERNAME` and `INSIGHTSQL_PASSWORD`: set both to enable HTTP Basic authentication. Required for non-loopback binding; HTTPS is mandatory for remote use.
- `INSIGHTSQL_RATE_LIMIT_PER_MINUTE`: per-IP endpoint cap (default 30). For multiple replicas, enforce a shared limit at the ingress/gateway as well.

## Project layout

```text
insightsql/          FastAPI routes, config, AI client, SQL policy, history
static/              Responsive browser workbench
tests/               API and SQL security regression tests
employee_management.db  Read-only query source
data/                Local query history (ignored by Git)
```

## Known scope and production considerations

InsightSQL is a secured local/demo application, not a multi-tenant HR analytics service. HTTP Basic is intentionally a small local access gate, not a substitute for OIDC/MFA. The in-memory rate limiter is per process. Before remote or multi-user production deployment, replace local authentication with an enterprise identity provider and role/row-level authorization, add distributed rate limiting, TLS ingress, secrets management, security monitoring, and a reviewed policy for AI-provider data handling.