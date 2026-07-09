"""Per-call tracing.

One JSONL line per call in results/calls/traces.jsonl: which provider the
router picked, every notable event with a millisecond offset, and how the
call ended. This is the artifact that answers "the bot felt laggy yesterday
— which stage ate the budget?"
"""

import json
from datetime import datetime, timezone
from time import monotonic

from loguru import logger

from switchboard.config import CALLS_DIR

TRACES_PATH = CALLS_DIR / "traces.jsonl"


class CallTrace:
    def __init__(self, call_sid: str, playbook: str, tts_provider: str, router_snapshot: dict):
        self._t0 = monotonic()
        self._record = {
            "call_sid": call_sid,
            "playbook": playbook,
            "tts_provider": tts_provider,
            "router": router_snapshot,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "events": [],
            "status": "in_progress",
        }
        self.event("call_started")

    def event(self, name: str, **fields) -> None:
        entry = {"t_ms": round((monotonic() - self._t0) * 1000, 1), "event": name, **fields}
        self._record["events"].append(entry)
        logger.info(f"[{self._record['call_sid']}] {entry}")

    def close(self, status: str = "completed") -> None:
        self._record["status"] = status
        self._record["duration_s"] = round(monotonic() - self._t0, 1)
        CALLS_DIR.mkdir(parents=True, exist_ok=True)
        with TRACES_PATH.open("a") as f:
            f.write(json.dumps(self._record) + "\n")
