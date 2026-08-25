from server import app


def test_index_returns_ok():
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200


def test_list_cases_returns_summary():
    client = app.test_client()
    response = client.get("/api/cases")
    assert response.status_code == 200
    data = response.get_json()
    assert "summary" in data
    assert "open_count" in data["summary"]
