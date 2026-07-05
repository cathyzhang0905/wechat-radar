"""Lightweight run state tracking for wechat-radar.

The goal is observability, not orchestration. Every production run writes a
small JSON file that answers: which stage are we in, what failed, and why.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
DEFAULT_STAGES = [
    "fetch",
    "parse",
    "dedup",
    "store",
    "summarize",
    "newsletter_generate",
    "email_send",
]


def now_cst() -> str:
    return datetime.now(CST).isoformat()


class RunState:
    def __init__(self, path: str | Path = "run_state.json", run_id: str | None = None):
        self.path = Path(path)
        self.run_id = run_id or os.getenv("GITHUB_RUN_ID") or uuid.uuid4().hex[:12]
        self.data = {
            "run_id": self.run_id,
            "status": "running",
            "started_at": now_cst(),
            "updated_at": now_cst(),
            "finished_at": None,
            "stages": {
                name: {
                    "status": "pending",
                    "started_at": None,
                    "finished_at": None,
                    "duration_sec": None,
                    "summary": "",
                    "error": "",
                    "retries": [],
                }
                for name in DEFAULT_STAGES
            },
        }
        self.save()
        logger.info("RUN_STATE run_id=%s path=%s", self.run_id, self.path)

    def save(self) -> None:
        self.data["updated_at"] = now_cst()
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self, stage: str, summary: str = "") -> None:
        item = self._stage(stage)
        item.update({
            "status": "running",
            "started_at": now_cst(),
            "finished_at": None,
            "duration_sec": None,
            "summary": summary,
            "error": "",
            "_start_monotonic": time.monotonic(),
        })
        logger.info("STATE %s=start run_id=%s %s", stage, self.run_id, summary)
        self.save()

    def pass_stage(self, stage: str, summary: str = "") -> None:
        item = self._stage(stage)
        self._finish_item(item, "pass", summary, "")
        logger.info("STATE %s=pass run_id=%s %s", stage, self.run_id, summary)
        self.save()

    def fail_stage(self, stage: str, error: str, summary: str = "") -> None:
        item = self._stage(stage)
        self._finish_item(item, "fail", summary, error)
        self.data["status"] = "fail"
        self.data["finished_at"] = now_cst()
        logger.error("STATE %s=fail run_id=%s error=%s", stage, self.run_id, error)
        self.save()

    def skip_stage(self, stage: str, summary: str = "") -> None:
        item = self._stage(stage)
        self._finish_item(item, "skipped", summary, "")
        logger.info("STATE %s=skipped run_id=%s %s", stage, self.run_id, summary)
        self.save()

    def record_retry(self, stage: str, attempt: int, max_attempts: int, error: str) -> None:
        item = self._stage(stage)
        item["retries"].append({
            "attempt": attempt,
            "max_attempts": max_attempts,
            "at": now_cst(),
            "error": error,
        })
        logger.warning(
            "STATE %s=retry run_id=%s attempt=%s/%s error=%s",
            stage,
            self.run_id,
            attempt,
            max_attempts,
            error,
        )
        self.save()

    def complete(self, summary: str = "") -> None:
        if self.data["status"] != "fail":
            self.data["status"] = "pass"
        self.data["finished_at"] = now_cst()
        if summary:
            self.data["summary"] = summary
        logger.info("STATE run=complete run_id=%s status=%s %s", self.run_id, self.data["status"], summary)
        self.save()

    @contextmanager
    def stage(self, stage: str, summary: str = "") -> Iterator[None]:
        self.start(stage, summary)
        try:
            yield
        except Exception as exc:
            self.fail_stage(stage, str(exc), summary)
            raise

    def _stage(self, stage: str) -> dict:
        if stage not in self.data["stages"]:
            self.data["stages"][stage] = {
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "duration_sec": None,
                "summary": "",
                "error": "",
                "retries": [],
            }
        return self.data["stages"][stage]

    @staticmethod
    def _finish_item(item: dict, status: str, summary: str, error: str) -> None:
        item["status"] = status
        item["finished_at"] = now_cst()
        if summary:
            item["summary"] = summary
        item["error"] = error
        started = item.pop("_start_monotonic", None)
        if started is not None:
            item["duration_sec"] = round(time.monotonic() - started, 3)


def retry_call(
    stage: str,
    state: RunState | None,
    func,
    *args,
    retries: int = 3,
    delay_sec: float = 2.0,
    retry_false: bool = False,
    **kwargs,
):
    """Retry an external dependency call and record every failed attempt."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            result = func(*args, **kwargs)
            if retry_false and result is False:
                raise RuntimeError(f"{getattr(func, '__name__', 'call')} returned False")
            return result
        except Exception as exc:
            last_error = exc
            if state:
                state.record_retry(stage, attempt, retries, str(exc))
            if attempt < retries:
                time.sleep(delay_sec * attempt)
    raise last_error  # type: ignore[misc]
