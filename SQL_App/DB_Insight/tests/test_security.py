from __future__ import annotations

import sqlite3

import pytest

from insightsql.database import QueryRejected, ReadOnlyDatabase


@pytest.fixture
def database(test_settings):
    return ReadOnlyDatabase(test_settings.database_path, max_rows=1, timeout_seconds=1.0)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO employees VALUES (3, 1, 'x', 'y', 'x@y.test', 1, 'active')",
        "UPDATE employees SET salary = 0",
        "DELETE FROM employees",
        "DROP TABLE employees",
        "ALTER TABLE employees ADD COLUMN secret TEXT",
        "PRAGMA table_info(employees)",
        "ATTACH DATABASE 'other.db' AS other",
        "SELECT 1; DELETE FROM employees",
        "SELECT name FROM sqlite_master",
        "SELECT load_extension('malicious')",
        "SELECT * FROM unknown_table",
        "SELECT * FROM main.sqlite_schema",
    ],
)
def test_rejects_non_select_or_unapproved_access(database, sql):
    with pytest.raises(QueryRejected):
        database.execute(sql)


def test_allows_select_and_enforces_row_limit(database):
    result = database.execute("SELECT first_name FROM employees ORDER BY employee_id")
    assert result["rows"] == [{"first_name": "Ava"}]
    assert result["row_count"] == 1
    assert result["truncated"] is True


def test_allows_cte_and_union_selects(database):
    cte_result = database.execute(
        "WITH active_staff AS (SELECT first_name FROM employees WHERE employment_status = 'active') "
        "SELECT first_name FROM active_staff ORDER BY first_name"
    )
    union_result = database.execute(
        "SELECT first_name FROM employees WHERE employee_id = 1 "
        "UNION SELECT first_name FROM employees WHERE employee_id = 2"
    )
    assert cte_result["rows"] == [{"first_name": "Ava"}]
    assert union_result["row_count"] == 1
    assert union_result["truncated"] is True


def test_clamps_requested_limit(database):
    result = database.execute("SELECT first_name FROM employees LIMIT 100000")
    assert result["row_count"] == 1
    assert result["truncated"] is True


def test_unknown_sql_function_is_blocked(database):
    with pytest.raises(QueryRejected):
        database.execute("SELECT writefile('x.txt', 'x')")


def test_internal_schema_access_is_blocked_by_authorizer(database):
    with pytest.raises(QueryRejected):
        database.execute("SELECT name FROM sqlite_master")


def test_long_queries_are_rejected(database):
    with pytest.raises(QueryRejected):
        database.validate("SELECT " + "1 " * 5000)


def test_database_file_is_read_only(database, test_settings):
    with pytest.raises(sqlite3.OperationalError):
        with sqlite3.connect(f"file:{test_settings.database_path.as_posix()}?mode=ro", uri=True) as connection:
            connection.execute("DELETE FROM employees")