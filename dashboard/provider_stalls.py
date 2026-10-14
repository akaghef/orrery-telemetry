"""Legacy tmux provider-stop observation; never resumes or types into agents."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid

CAPACITY = "■ Selected model is at capacity. Please try a different model."
_ERROR = re.compile(r"(?m)^\s*■ Selected model is at capacity\.\s+Please try a different model\.[ \t]*$")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_WORK = re.compile(r"(?im)^\s*[•▌●]\s*(?:Working|Thinking)\b[^\n]*\([^\n]*(?:\d+\s*s|esc to interrupt)")
_FOOTER = re.compile(r"^(?:gpt-\S+|sol|luna|astra|terra)\s+(?:low|medium|high|xhigh|max|ultra)\b.*$", re.I)
_UI = re.compile(r"^(?:[─━\s]+|\? for shortcuts.*|\d+% context left.*)$")


def classify(text: str) -> dict:
    """Only an exact terminal error immediately above the current prompt counts."""
    text = _ANSI.sub("", text).replace("\r", "")
    errors = list(_ERROR.finditer(text))
    if errors:
        suffix = [line.strip() for line in text[errors[-1].end():].splitlines() if line.strip()]
        prompts = [i for i, line in enumerate(suffix) if line.startswith("›")]
        if len(prompts) == 1 and prompts[0] == 0 and all(
            _FOOTER.fullmatch(line) or _UI.fullmatch(line)
            for line in suffix[1:]
        ):
            return {"state": "capacity", "error": CAPACITY}
    # Historical work before a terminal error cannot rearm a stopped episode.
    tail = text[errors[-1].end():] if errors else "\n".join(text.splitlines()[-16:])
    if _WORK.search(tail):
        return {"state": "work"}
    return {"state": "idle" if re.search(r"(?m)^\s*›", tail) else "unknown"}


class Monitor:
    """A durable event claim bounds each episode to one Mail send attempt."""

    def __init__(self, path: Path, send):
        self.path = Path(path)
        self.send = send
        self._lock = threading.RLock()
        self._rows = {}
        self._views = {}

    @contextmanager
    def _transaction(self):
        import fcntl  # The worker is legacy POSIX-only; keep global/Windows imports safe.
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("unsafe provider-stall lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                state_fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                self._rows = {}
            else:
                with os.fdopen(state_fd, encoding="utf-8") as source:
                    info = os.fstat(source.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 2_000_000):
                        raise ValueError("unsafe provider-stall state")
                    saved = json.load(source)
                if saved.get("version") != 1 or not isinstance(saved.get("events"), dict):
                    raise ValueError("invalid provider-stall state")
                self._rows = saved["events"]
            yield
        finally:
            os.close(fd)

    def _save(self):
        fd, name = tempfile.mkstemp(prefix=".provider-stalls-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump({"version": 1, "events": self._rows}, target, ensure_ascii=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(name, self.path)
            # HTTP reads never wait for Mail or an inter-process state lock.
            self._views = {
                (row["name"], row["generation"]): {
                    key: row[key] for key in ("kind", "since", "error", "notification_status")
                } for row in self._rows.values() if row["phase"] == "stalled"
            }
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def tick(self, samples, *, now=None):
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        samples = list(samples)
        with self._lock, self._transaction():
            for row in samples:
                key = json.dumps([row["project_key"], row["agent_id"], row["generation"]])
                old = self._rows.get(key)
                if old:
                    old["last_seen"] = now
                state = row["evidence"].get("state", "unknown")
                if state == "work":
                    self._rows.pop(key, None)
                    continue
                if state != "capacity":
                    if old and old["phase"] == "candidate":
                        self._rows.pop(key, None)
                    continue
                if old is None:
                    self._rows[key] = dict(name=row["name"], generation=row["generation"],
                        phase="candidate", capture_id=row["capture_id"], first_seen=now,
                        last_seen=now)
                    continue
                old["last_seen"] = now
                if old["phase"] != "candidate" or old["capture_id"] == row["capture_id"]:
                    continue
                old.update(phase="stalled", kind="model_capacity", error=CAPACITY,
                           event_id=str(uuid.uuid4()), since=now,
                           notification_status="delivery_unknown")
                # Commit the claim before a call whose result could be lost.
                self._save()
                if not row.get("parent"):
                    old["notification_status"] = "no_parent"
                else:
                    try:
                        result = self.send(row, dict(old))
                        old["notification_status"] = result if result in {
                            "sent", "failed", "parent_unverified", "delivery_unknown"
                        } else "delivery_unknown"
                    except Exception:
                        old["notification_status"] = "delivery_unknown"
                        logging.warning("Provider-stop delivery result is unknown for %s", row["name"])
            # A replacement generation is positive evidence. Mere absence or a
            # long dashboard outage must never rearm the same stopped session.
            current = {(row["project_key"], row["agent_id"]): row["generation"] for row in samples}
            for key, value in list(self._rows.items()):
                project, agent_id, generation = json.loads(key)
                replacement = current.get((project, agent_id), generation)
                if now - value["last_seen"] >= 7 * 86400 and replacement != generation:
                    del self._rows[key]
            self._save()

    def view(self, name, generation):
        row = self._views.get((name, generation))
        return dict(row) if row else None

    def run(self, sample, stop, *, interval=5):
        while not stop.is_set():
            try:
                self.tick(sample())
            except Exception:
                logging.exception("Provider-stop observation failed")
            stop.wait(interval)


def message(event: dict) -> str:
    when = datetime.fromtimestamp(event["since"], timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    return ("telemetry の自動検知\n\n"
            f"検知時刻: {when} (JST)\n"
            f"{event['error']}\n\n"
            f"Event: {event['event_id']}\n自動再開はしていません。")
