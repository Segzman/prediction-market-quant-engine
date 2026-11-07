from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class AlertManager:
    alerts_file: str
    webhook_url: str = ""

    def emit(self, level: str, message: str, **context: Any) -> dict[str, Any]:
        payload = {
            "ts": time.time(),
            "level": level.upper(),
            "message": message,
            "context": context,
        }
        self._append(payload)
        if self.webhook_url.strip():
            self._send_webhook(payload)
        return payload

    def _append(self, payload: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.alerts_file) or ".", exist_ok=True)
        with open(self.alerts_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def _send_webhook(
        self, payload: dict[str, Any], *, max_attempts: int = 3, backoff: float = 0.5
    ) -> bool:
        """Best-effort webhook delivery with retry.

        Never raises: a failed delivery must not crash alerting (the local
        jsonl record is already persisted). On final failure it logs a warning
        so the miss is at least visible in the bot log. Returns True on success.
        """
        body = json.dumps(payload).encode("utf-8")
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                request = urllib.request.Request(
                    self.webhook_url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5):
                    return True
            except Exception as exc:  # noqa: BLE001 - delivery is best-effort
                last_exc = exc
                if attempt + 1 < max_attempts:
                    time.sleep(backoff * (2 ** attempt))
        _LOGGER.warning(
            "Alert webhook delivery failed after %d attempts: %s", max_attempts, last_exc
        )
        return False
