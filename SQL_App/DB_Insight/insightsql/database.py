from __future__ import annotations

import sqlite3
import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_SQL_LENGTH = 8_000
MAX_RESULT_BYTES = 2_000_000
ALLOWED_TABLES = frozenset({"department", "employees", "projects", "employee_projects"})
ALLOWED_FUNCTIONS = frozenset(
    {
        "ABS", "AVG", "COALESCE", "COUNT", "DATE", "DATETIME", "IFNULL", "LENGTH", "LOWER",
        "LTRIM", "MAX", "MIN", "NULLIF", "ROUND", "RTRIM", "STRFTIME", "SUBSTR", "SUM",
        "TIME", "TRIM", "TOTAL", "UPPER",
    }
)


class QueryRejected(Exception):
    """The SQL violates the application read-only policy."""


class QueryTimedOut(Exception):
    """The query exceeded its execution budget."""


class QueryExecutionFailed(Exception):
    """The database could not execute a policy-approved query."""


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str


class ReadOnlyDatabase:
    def __init__(self, path: Path, max_rows: int, timeout_seconds: float) -> None:
        self.path = path.resolve()
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        if not self.path.is_file():
            raise FileNotFoundError(f"SQLite database does not exist: {self.path}")
        self._schema = self._load_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 256_000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_LENGTH)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 64)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 10)
        return connection

    def _load_schema(self) -> dict[str, tuple[tuple[str, str], ...]]:
        with self._connect() as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            schema: dict[str, tuple[tuple[str, str], ...]] = {}
            for row in tables:
                table_name = str(row["name"]).lower()
                if table_name not in ALLOWED_TABLES:
                    continue
                columns = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                schema[table_name] = tuple((str(column["name"]), str(column["type"])) for column in columns)
        return schema

    def schema_description(self) -> str:
        if not self._schema:
            return "No approved business tables are available."
        lines = []
        for table_name, columns in sorted(self._schema.items()):
            formatted_columns = ", ".join(f"{name} ({kind or 'value'})" for name, kind in columns)
            lines.append(f"{table_name}: {formatted_columns}")
        return "\n".join(lines)

    def validate(self, sql: str) -> ValidatedQuery:
        if not sql or not sql.strip():
            raise QueryRejected("Enter a SQL query.")
        if len(sql) > MAX_SQL_LENGTH:
            raise QueryRejected(f"SQL is too long. Maximum length is {MAX_SQL_LENGTH} characters.")
        try:
            statements = [statement for statement in sqlglot.parse(sql, read="sqlite") if statement is not None]
        except ParseError as exc:
            raise QueryRejected("The SQL could not be parsed. Check the syntax and try again.") from exc
        if len(statements) != 1:
            raise QueryRejected("Only one SQL statement is allowed at a time.")

        statement = statements[0]
        if not isinstance(statement, (exp.Select, exp.SetOperation)):
            raise QueryRejected("Only read-only SELECT queries are allowed.")

        cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
        for table in statement.find_all(exp.Table):
            name = table.name.lower()
            if table.catalog or table.db not in {"", "main"}:
                raise QueryRejected("Only approved tables in the main database are available.")
            if name not in self._schema and name not in cte_names:
                raise QueryRejected(f"Table '{name}' is not available to this application.")

        for function in statement.find_all(exp.Func):
            try:
                name = function.sql_name().upper()
            except Exception:
                name = ""
            if name in {"", "ANONYMOUS"}:
                name = str(getattr(function, "name", "")).upper()
            if name not in ALLOWED_FUNCTIONS:
                raise QueryRejected(f"Function '{name or 'unknown'}' is not allowed.")

        limit = statement.args.get("limit")
        enforced_limit = self.max_rows + 1
        if limit is None:
            statement.set("limit", exp.Limit(expression=exp.Literal.number(enforced_limit)))
        else:
            limit_value = limit.expression
            if not isinstance(limit_value, exp.Literal) or limit_value.is_string:
                raise QueryRejected("LIMIT must be a non-negative integer literal.")
            try:
                requested_limit = int(limit_value.this)
            except (TypeError, ValueError) as exc:
                raise QueryRejected("LIMIT must be a non-negative integer literal.") from exc
            if requested_limit < 0:
                raise QueryRejected("LIMIT must be non-negative.")
            limit.set("expression", exp.Literal.number(min(requested_limit, enforced_limit)))

        offset = statement.args.get("offset")
        if offset is not None:
            offset_value = offset.expression
            if not isinstance(offset_value, exp.Literal) or offset_value.is_string:
                raise QueryRejected("OFFSET must be an integer literal no greater than 10000.")
            try:
                if not 0 <= int(offset_value.this) <= 10_000:
                    raise QueryRejected("OFFSET must be between 0 and 10000.")
            except (TypeError, ValueError) as exc:
                raise QueryRejected("OFFSET must be an integer literal no greater than 10000.") from exc

        return ValidatedQuery(sql=statement.sql(dialect="sqlite"))

    def _authorizer(self, action: int, arg1: str | None, arg2: str | None, database: str | None, source: str | None) -> int:
        del database, source
        allowed_actions = {sqlite3.SQLITE_SELECT}
        recursive_action = getattr(sqlite3, "SQLITE_RECURSIVE", None)
        if recursive_action is not None:
            allowed_actions.add(recursive_action)
        if action in allowed_actions:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table_name = (arg1 or "").lower()
            column_name = arg2 or ""
            columns = {column for column, _ in self._schema.get(table_name, ())}
            return sqlite3.SQLITE_OK if table_name in self._schema and (not column_name or column_name in columns) else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (arg2 or arg1 or "").upper()
            return sqlite3.SQLITE_OK if function_name in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def execute(self, sql: str) -> dict[str, Any]:
        validated = self.validate(sql)
        deadline = time.monotonic() + self.timeout_seconds
        instructions = 0

        def progress() -> int:
            nonlocal instructions
            instructions += 1_000
            return int(time.monotonic() >= deadline or instructions >= 2_000_000)

        try:
            with self._connect() as connection:
                connection.set_authorizer(self._authorizer)
                connection.set_progress_handler(progress, 1_000)
                cursor = connection.execute(validated.sql)
                columns = [column[0] for column in cursor.description or ()]
                rows: list[dict[str, Any]] = []
                result_bytes = len(json.dumps(columns, ensure_ascii=False).encode("utf-8"))
                truncated = False
                while len(rows) <= self.max_rows:
                    fetched = cursor.fetchone()
                    if fetched is None:
                        break
                    if len(rows) == self.max_rows:
                        truncated = True
                        break
                    row = {
                        key: self._json_value(value)
                        for key, value in zip(columns, tuple(fetched))
                    }
                    row_bytes = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                    if result_bytes + row_bytes > MAX_RESULT_BYTES:
                        truncated = True
                        break
                    rows.append(row)
                    result_bytes += row_bytes
                return {
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": truncated,
                    "sql": validated.sql,
                }
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "interrupted" in message or time.monotonic() >= deadline:
                raise QueryTimedOut("The query exceeded its execution limit. Narrow the query and try again.") from exc
            if "not authorized" in message:
                raise QueryRejected("SQLite blocked this query under the read-only access policy.") from exc
            raise QueryExecutionFailed("SQLite could not run this query. Check the table, columns, and SQL syntax.") from exc
        except sqlite3.Error as exc:
            raise QueryExecutionFailed("SQLite could not run this query. Check the table, columns, and SQL syntax.") from exc