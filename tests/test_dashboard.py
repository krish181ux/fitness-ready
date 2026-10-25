"""The dashboard's two routes on the tools server: the page, and /chat to n8n."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest


def test_dashboard_is_served_at_the_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>Ready</title>" in response.text
    assert client.get("/app.js").status_code == 200


def test_tool_routes_still_win_over_the_static_mount(client):
    assert client.get("/health").json()["status"] == "ok"


def test_chat_relays_one_message_and_the_agents_reply(client, monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout):
        sent.update(json.loads(request.data))
        return io.BytesIO(json.dumps({"output": "Rest today."}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    body = client.post("/chat", json={"session_id": "s1", "message": "Should I train?"}).json()
    assert body == {"reply": "Rest today."}
    assert sent == {"action": "sendMessage", "sessionId": "s1", "chatInput": "Should I train?"}


def test_chat_accepts_n8n_answering_with_a_list(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda request, timeout: io.BytesIO(b'[{"output": "Easy day."}]'))
    assert client.post("/chat", json={"session_id": "s", "message": "hi"}).json() == {"reply": "Easy day."}


@pytest.mark.parametrize("error, expected", [
    (urllib.error.URLError("connection refused"), "docker compose up"),
    (urllib.error.HTTPError("http://n8n", 404, "Not Found", {}, None), "Make Chat Publicly Available"),
    (urllib.error.HTTPError("http://n8n", 500, "Server Error", {}, None), "HTTP 500"),
])
def test_chat_says_what_went_wrong_with_n8n(client, monkeypatch, error, expected):
    def fail(request, timeout):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)
    response = client.post("/chat", json={"session_id": "s", "message": "hi"})
    assert response.status_code == 502
    assert expected in response.json()["detail"]


def test_chat_reports_an_empty_reply_rather_than_showing_nothing(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: io.BytesIO(b'{"output": ""}'))
    response = client.post("/chat", json={"session_id": "s", "message": "hi"})
    assert response.status_code == 502


def test_chat_rejects_an_empty_message(client):
    assert client.post("/chat", json={"session_id": "s", "message": ""}).status_code == 422
