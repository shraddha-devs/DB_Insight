from fastapi.testclient import TestClient


def test_query_returns_rows_and_records_history(test_app):
    with TestClient(test_app) as client:
        response = client.post(
            "/api/query",
            json={"question": "List employees", "sql": "SELECT first_name FROM employees ORDER BY employee_id"},
        )
        assert response.status_code == 200
        assert response.json()["rows"] == [{"first_name": "Ava"}]
        assert response.json()["truncated"] is True
        history = client.get("/api/history")
        assert history.status_code == 200
        assert len(history.json()["items"]) == 1
        assert history.json()["items"][0]["question"] == "List employees"


def test_api_rejects_write_query(test_app):
    with TestClient(test_app) as client:
        response = client.post("/api/query", json={"sql": "DELETE FROM employees"})
        assert response.status_code == 400
        assert response.json()["code"] == "query_rejected"


def test_generate_explains_missing_api_key(test_app):
    with TestClient(test_app) as client:
        response = client.post("/api/generate", json={"question": "How many employees are there?"})
        assert response.status_code == 503
        assert response.json()["code"] == "ai_unavailable"


def test_schema_endpoint_exposes_only_approved_tables(test_app):
    with TestClient(test_app) as client:
        response = client.get("/api/schema")
        assert response.status_code == 200
        names = {table["name"] for table in response.json()["tables"]}
        assert names == {"department", "employees", "projects", "employee_projects"}


def test_history_can_be_cleared(test_app):
    with TestClient(test_app) as client:
        client.post("/api/query", json={"sql": "SELECT COUNT(*) AS count FROM employees"})
        response = client.delete("/api/history")
        assert response.status_code == 200
        assert client.get("/api/history").json()["items"] == []


def test_request_body_size_is_bounded(test_app):
    with TestClient(test_app) as client:
        response = client.post(
            "/api/query",
            content=b"{" + b" " * 33_000 + b"}",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "request_too_large"