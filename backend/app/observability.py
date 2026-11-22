from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("copilot")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class SessionLog:

    _lock = threading.Lock()

    def __init__(self, log_dir: Path | None, session_id: str, trace_id: str):
        self.session_id = session_id
        self.trace_id = trace_id
        self.path: Path | None = None
        if log_dir is not None:
            self.path = log_dir / "sessions" / f"{session_id}.jsonl"

    def event(self, event: str, **data: Any) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "event": event,
            **data,
        }
        brief = {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))}
        logger.info("%s %s", event, json.dumps({"trace_id": self.trace_id, **brief},
                                               ensure_ascii=False)[:500])
        if self.path is None:
            return
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            logger.exception("failed to write session log")
