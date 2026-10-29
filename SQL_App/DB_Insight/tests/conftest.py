from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from insightsql.config import Settings
from insightsql.main import create_app


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    database_path = tmp_path / "test.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE department (department_id INTEGER PRIMARY KEY, department_name TEXT NOT NULL UNIQUE);
            CREATE TABLE employees (employee_id INTEGER PRIMARY KEY, department_id INTEGER NOT NULL, first_name TEXT, last_name TEXT, email TEXT UNIQUE, salary NUMERIC, employment_status TEXT, FOREIGN KEY (department_id) REFERENCES department(department_id));
            CREATE TABLE projects (project_id INTEGER PRIMARY KEY, project_code TEXT UNIQUE, project_name TEXT);
            CREATE TABLE employee_projects (employee_id INTEGER, project_id INTEGER, PRIMARY KEY (employee_id, project_id));
            INSERT INTO department VALUES (1, 'Engineering');
            INSERT INTO employees VALUES (1, 1, 'Ava', 'Patel', 'ava@example.com', 98000, 'active');
            INSERT INTO employees VALUES (2, 1, 'Noah', 'Kim', 'noah@example.com', 112000, 'active');
            INSERT INTO projects VALUES (1, 'APP', 'Example App');
            INSERT INTO employee_projects VALUES (1, 1);
            """
        )
    return Settings(
        database_path=database_path,
        history_path=tmp_path / "history.sqlite3",
        openai_api_key=None,
        openai_model="test-model",
        openai_chat_completions_url="https://example.invalid/v1/chat/completions",
        host="127.0.0.1",
        port=8000,
        username=None,
        password=None,
        allowed_hosts=("testserver", "localhost", "127.0.0.1"),
        max_rows=1,
        query_timeout_seconds=1.0,
        rate_limit_per_minute=30,
    )


@pytest.fixture
def test_app(test_settings: Settings):
    return create_app(test_settings)