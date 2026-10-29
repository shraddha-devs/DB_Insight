from __future__ import annotations

import asyncio

from insightsql.ai import generate_sql
from insightsql.config import Settings


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self.content}}]}


class FakeAsyncClient:
    response_content = "SELECT COUNT(*) FROM employees"
    request_payload: dict | None = None

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        type(self).request_payload = {"url": url, "headers": headers, "json": json}
        return FakeResponse(self.response_content)


def test_ai_adapter_sends_question_and_schema_without_database_rows(monkeypatch, test_settings):
    settings = Settings(**{**test_settings.__dict__, "openai_api_key": "test-key"})
    monkeypatch.setattr("insightsql.ai.httpx.AsyncClient", FakeAsyncClient)

    sql = asyncio.run(generate_sql("How many employees?", "employees: employee_id", settings))

    assert sql == "SELECT COUNT(*) FROM employees"
    assert FakeAsyncClient.request_payload["headers"]["Authorization"] == "Bearer test-key"
    sent_messages = FakeAsyncClient.request_payload["json"]["messages"]
    assert "How many employees?" in sent_messages[1]["content"]
    assert "employees: employee_id" in sent_messages[1]["content"]


def test_ai_adapter_removes_sql_markdown_fence(monkeypatch, test_settings):
    settings = Settings(**{**test_settings.__dict__, "openai_api_key": "test-key"})
    FakeAsyncClient.response_content = "```sql\nSELECT 1\n```"
    monkeypatch.setattr("insightsql.ai.httpx.AsyncClient", FakeAsyncClient)

    try:
        assert asyncio.run(generate_sql("Return one", "", settings)) == "SELECT 1"
    finally:
        FakeAsyncClient.response_content = "SELECT COUNT(*) FROM employees"