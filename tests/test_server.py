"""Webhook auth, WebSocket gating, and demo guards — no Twilio account needed.

These run against the real FastAPI app via TestClient; the pipecat pipeline
import inside /ws is deferred, so none of this needs the agent extra's heavy
dependencies installed.
"""

import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import switchboard.agent.server as server

AUTH_TOKEN = "test-auth-token"
HOST = "demo.example.com"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PUBLIC_HOST", HOST)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", AUTH_TOKEN)
    # Fresh daily counter per test
    monkeypatch.setitem(server._calls_today, "count", 0)
    return TestClient(server.app)


def sign(form: dict) -> str:
    payload = f"https://{HOST}/voice" + "".join(k + v for k, v in sorted(form.items()))
    return base64.b64encode(
        hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()


def test_health_reports_router_state(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "ranked_now" in body["router"]


def test_voice_rejects_missing_signature(client):
    response = client.post("/voice", data={"CallSid": "CA123"})
    assert response.status_code == 403


def test_voice_rejects_tampered_signature(client):
    form = {"CallSid": "CA123"}
    headers = {"X-Twilio-Signature": sign({"CallSid": "CA-tampered"})}
    assert client.post("/voice", data=form, headers=headers).status_code == 403


def test_voice_accepts_valid_signature_and_returns_stream_twiml(client):
    form = {"CallSid": "CA123", "From": "+15550001111"}
    response = client.post("/voice", data=form, headers={"X-Twilio-Signature": sign(form)})
    assert response.status_code == 200
    body = response.text
    assert f'<Stream url="wss://{HOST}/ws?token=' in body
    # The ws token must be derived, never the raw auth token.
    assert AUTH_TOKEN not in body


def test_daily_cap_declines_politely(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_CALLS_PER_DAY", 1)
    form = {"CallSid": "CA123"}
    headers = {"X-Twilio-Signature": sign(form)}
    assert "<Stream" in client.post("/voice", data=form, headers=headers).text
    second = client.post("/voice", data=form, headers=headers)
    assert second.status_code == 200  # still valid TwiML, spoken decline
    assert "daily call limit" in second.text
    assert "<Stream" not in second.text


def test_ws_rejects_bad_token(client):
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/ws?token=wrong"),
    ):
        pass
    assert exc_info.value.code == 4403


def test_ws_closes_when_no_start_event_arrives(client, monkeypatch):
    monkeypatch.setattr(server, "WS_HANDSHAKE_TIMEOUT_S", 0.2)
    with client.websocket_connect(f"/ws?token={server._ws_token()}") as ws:
        message = ws.receive()  # server should close after the handshake timeout
        assert message["type"] == "websocket.close"
        assert message["code"] == 4408
