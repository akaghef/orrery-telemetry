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

    def __init__(self, path: Path, send, *, session_names=None):
        self.path = Path(path)
        self.send = send
        self._lock = threading.RLock()
        self._rows = {}
        self._views = {}
        self._saved_text = None
        self._session_names = session_names

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
                self._saved_text = None
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
                self._saved_text = json.dumps(saved, ensure_ascii=False, sort_keys=True)
            yield
        finally:
            os.close(fd)

    def _save(self):
        text = json.dumps({"version": 1, "events": self._rows}, ensure_ascii=False, sort_keys=True)
        # Publish even when another monitor saved the same state in between.
        self._views = {
            (row["name"], row["generation"]): {
                key: row[key] for key in ("kind", "since", "error", "notification_status")
            } for row in self._rows.values() if row["phase"] == "stalled"
        }
        if text == self._saved_text:
            return
        fd, name = tempfile.mkstemp(prefix=".provider-stalls-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                target.write(text)
                target.flush()
                os.fsync(target.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
                self._saved_text = text
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def tick(self, samples, *, now=None):
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        samples = list(samples)
        # No capacity candidate and no journal: do not even create a lock/root.
        if not os.path.lexists(self.path) and not any(
            row["evidence"].get("state") == "capacity" for row in samples
        ):
            return
        with self._lock, self._transaction():
            for row in samples:
                key = json.dumps([row["project_key"], row["agent_id"], row["generation"]])
                old = self._rows.get(key)
                capture = row["capture_id"]
                # A delayed scrape must not undo or confirm a newer transition.
                if old and capture <= old["capture_id"]:
                    continue
                if old and now - old["last_seen"] >= 3600:
                    old["last_seen"] = now
                state = row["evidence"].get("state", "unknown")
                if state == "work":
                    if old:
                        self._rows[key] = dict(name=row["name"], generation=row["generation"],
                            phase="clear", capture_id=capture, last_seen=now)
                    continue
                if state != "capacity":
                    if old and old["phase"] == "candidate":
                        old.update(phase="clear", capture_id=capture)
                    continue
                if old is None or old["phase"] == "clear":
                    self._rows[key] = dict(name=row["name"], generation=row["generation"],
                        phase="candidate", capture_id=capture, first_seen=now,
                        last_seen=now)
                    continue
                if old["phase"] != "candidate":
                    continue
                old.update(phase="stalled", capture_id=capture, kind="model_capacity", error=CAPACITY,
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
            # Only query definitive tmux inventory for rows past the expiry.
            expired = any(now - row["last_seen"] >= 30 * 86400 for row in self._rows.values())
            names = self._session_names() if expired and self._session_names else None
            for key, value in list(self._rows.items()):
                project, agent_id, generation = json.loads(key)
                replacement = current.get((project, agent_id), generation)
                age = now - value["last_seen"]
                if ((age >= 7 * 86400 and replacement != generation)
                        or (age >= 30 * 86400 and names is not None and value["name"] not in names)):
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
