"""FastAPI server: Twilio webhook + media-stream WebSocket.

Call flow:
1. Twilio number receives a call → POSTs to /voice
2. /voice returns TwiML telling Twilio to open a media stream to wss://…/ws
3. /ws negotiates the stream (connected → start events), then hands the
   socket to the pipecat pipeline for the rest of the call.

Local dev: `ngrok http 8080`, set PUBLIC_HOST to the ngrok domain, and point
the Twilio number's voice webhook at https://<PUBLIC_HOST>/voice.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
from datetime import date

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from loguru import logger

from switchboard.agent.playbooks import load_playbook
from switchboard.agent.router import LatencyRouter
from switchboard.config import env

# Public-demo guards. A reachable webhook with no auth means strangers can
# burn your API credits; these caps bound the worst case.
MAX_CALLS_PER_DAY = int(os.getenv("MAX_CALLS_PER_DAY", "50"))

# A client that connects to /ws but never sends Twilio's start event would
# otherwise hold the socket forever.
WS_HANDSHAKE_TIMEOUT_S = 10.0

app = FastAPI(title="switchboard")
router = LatencyRouter()

# In-memory daily counter — fine for a single-instance demo deployment.
_calls_today = {"date": date.today().isoformat(), "count": 0}


def _consume_call_slot() -> bool:
    """Count this call against the daily cap; False means the cap is hit."""
    today = date.today().isoformat()
    if _calls_today["date"] != today:
        _calls_today.update(date=today, count=0)
    _calls_today["count"] += 1
    return _calls_today["count"] <= MAX_CALLS_PER_DAY


def _ws_token() -> str | None:
    """Shared secret in the stream URL, since Twilio can't sign WebSockets."""
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        return None
    return hashlib.sha256(auth_token.encode()).hexdigest()[:16]


def _valid_twilio_signature(request: Request, form: dict) -> bool:
    """Verify X-Twilio-Signature: base64(HMAC-SHA1(auth_token, url + sorted params))."""
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        logger.warning("TWILIO_AUTH_TOKEN not set — skipping webhook signature validation")
        return True
    url = f"https://{env('PUBLIC_HOST')}/voice"
    payload = url + "".join(k + v for k, v in sorted(form.items()))
    expected = base64.b64encode(
        hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", ""))


@app.get("/health")
async def health():
    return {"status": "ok", "router": router.snapshot()}


@app.post("/voice")
async def voice(request: Request):
    form = dict(await request.form())
    if not _valid_twilio_signature(request, form):
        logger.warning("rejected /voice request with bad Twilio signature")
        return Response(status_code=403)

    if not _consume_call_slot():
        logger.warning(f"daily call cap ({MAX_CALLS_PER_DAY}) reached — declining call")
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Say>This demo has reached its daily call limit. "
            "Please try again tomorrow.</Say></Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    host = env("PUBLIC_HOST")
    token = _ws_token()
    stream_url = f"wss://{host}/ws" + (f"?token={token}" if token else "")
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Connect><Stream url="{stream_url}"/></Connect>'
        '<Pause length="20"/>'
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    expected_token = _ws_token()
    if expected_token and websocket.query_params.get("token") != expected_token:
        await websocket.close(code=4403)
        logger.warning("rejected /ws connection with bad token")
        return

    await websocket.accept()

    # Twilio sends {"event":"connected"} then {"event":"start", ...}.
    stream_sid = call_sid = None
    try:
        async with asyncio.timeout(WS_HANDSHAKE_TIMEOUT_S):
            while stream_sid is None:
                message = json.loads(await websocket.receive_text())
                if message.get("event") == "start":
                    stream_sid = message["start"]["streamSid"]
                    call_sid = message["start"].get("callSid", "unknown")
    except TimeoutError:
        logger.warning("closed /ws connection: no start event within handshake timeout")
        await websocket.close(code=4408)
        return

    from switchboard.agent.pipeline import run_call  # deferred: heavy pipecat import

    playbook = load_playbook(os.getenv("PLAYBOOK", "cod_confirmation"))
    logger.info(f"call {call_sid}: playbook={playbook.name}")
    try:
        await run_call(websocket, stream_sid, call_sid, playbook, router)
    except Exception:
        logger.exception(f"call {call_sid} failed")


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
