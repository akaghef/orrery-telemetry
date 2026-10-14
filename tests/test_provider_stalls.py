"""Provider failures are reported without restarting or typing into the child."""
from __future__ import annotations

import copy
import subprocess
import threading

import pytest

pytestmark = pytest.mark.usefixtures("isolated_provider_stall_runtime")

from dashboard import provider_stalls as stalls

ERROR = "■ Selected model is at capacity. Please try a different model."
IDLE = "\n› \n  sol medium · Context 80% left · ~/project\n"
CAPACITY = ERROR + IDLE
WORK = "• Working (1s • esc to interrupt)\n" + IDLE


def sample(capture=1, text=CAPACITY, **extra):
    return dict(name="ChildCurie", agent_id=2, project_key="/project",
                generation="101:$1:17", parent="ParentNewton", capture_id=capture,
                evidence=stalls.classify(text), **extra)


def test_current_terminal_error_only():
    assert stalls.classify(CAPACITY)["state"] == "capacity"
    assert stalls.classify(ERROR.replace("Please try", "\nPlease try") + IDLE)["state"] == "capacity"
    for text in (
        ERROR + "\n" + WORK,
        ERROR + "\n• All changes are verified.\n" + IDLE,
        "Example: " + CAPACITY,
        "```\n" + ERROR + "\n```\n" + IDLE,
        ERROR + "\n› continue the task\n" + IDLE,
        "■ API Error: overloaded\n" + IDLE,
        ERROR + "\nPress enter to confirm or esc to cancel\n",
    ):
        assert stalls.classify(text)["state"] != "capacity", text
    assert stalls.classify(WORK)["state"] == "work"


def test_two_captures_one_notice_restart_then_work_rearms(tmp_path):
    sent = []
    path = tmp_path / "runtime" / "provider-stalls.json"
    monitor = stalls.Monitor(path, lambda row, event: sent.append(copy.deepcopy(event)) or "sent")
    monitor.tick([sample(1)], now=100)
    monitor.tick([sample(1)], now=105)  # cached capture is not a second observation
    assert sent == []
    monitor.tick([sample(2)], now=110)
    assert len(sent) == 1
    assert monitor.view("ChildCurie", "101:$1:17")["notification_status"] == "sent"
    for n in range(3, 6):
        monitor.tick([sample(n)], now=110 + n)
    restarted = stalls.Monitor(path, lambda row, event: sent.append(event) or "sent")
    restarted.tick([sample(6)], now=120)
    restarted.tick([sample(7, text="")], now=125)  # unknown does not rearm
    restarted.tick([sample(8, text=IDLE)], now=130)  # idle does not rearm
    restarted.tick([sample(9)], now=135)
    assert len(sent) == 1
    restarted.tick([sample(10, text=WORK)], now=140)
    assert restarted.view("ChildCurie", "101:$1:17") is None
    restarted.tick([sample(11)], now=145)
    restarted.tick([sample(12)], now=150)
    assert len(sent) == 2
    assert sent[0]["event_id"] != sent[1]["event_id"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert "registration_token" not in path.read_text()


def test_parentless_has_marker_but_no_message(tmp_path):
    sent = []
    monitor = stalls.Monitor(tmp_path / "state.json", lambda *args: sent.append(args))
    row = sample()
    row["parent"] = ""
    monitor.tick([row], now=100)
    row["capture_id"] = 2
    monitor.tick([row], now=105)
    assert sent == []
    assert monitor.view(row["name"], row["generation"])["notification_status"] == "no_parent"


@pytest.mark.parametrize("result", ["delivery_unknown", "failed", "parent_unverified"])
def test_delivery_errors_are_not_retried(tmp_path, result):
    sent = []
    monitor = stalls.Monitor(tmp_path / "state.json", lambda *args: sent.append(args) or result)
    for n in range(1, 6):
        monitor.tick([sample(n)], now=100 + n * 5)
    assert len(sent) == 1
    assert monitor.view("ChildCurie", "101:$1:17")["notification_status"] == result


def test_generation_change_and_two_monitors_do_not_duplicate(tmp_path):
    sent = []
    path = tmp_path / "state.json"
    first = stalls.Monitor(path, lambda *args: sent.append(args) or "sent")
    second = stalls.Monitor(path, lambda *args: sent.append(args) or "sent")
    first.tick([sample(1)], now=100)
    second.tick([sample(2)], now=105)
    first.tick([sample(3)], now=110)
    assert len(sent) == 1
    row = sample(4)
    row["generation"] = "101:$2:18"
    first.tick([row], now=115)
    row["capture_id"] = 5
    second.tick([row], now=120)
    assert len(sent) == 2


def test_missing_capture_breaks_confirmation_without_clearing_stall(tmp_path):
    sent = []
    monitor = stalls.Monitor(tmp_path / "state.json", lambda *a: sent.append(a) or "sent")
    monitor.tick([sample(1)], now=100)
    monitor.tick([sample(2, text="")], now=105)
    monitor.tick([sample(3)], now=110)
    assert not sent
    monitor.tick([sample(4)], now=115)
    assert len(sent) == 1


def test_worker_runs_without_http_requests(tmp_path):
    sent = threading.Event()
    count = iter(range(1, 1000))
    monitor = stalls.Monitor(tmp_path / "state.json", lambda *a: sent.set() or "sent")
    stop = threading.Event()
    worker = threading.Thread(target=monitor.run, args=(lambda: [sample(next(count))], stop),
                              kwargs={"interval": .02})
    worker.start()
    try:
        assert sent.wait(2)
    finally:
        stop.set()
        worker.join(2)
    assert not worker.is_alive()


def test_isolated_real_tmux_capture(tmp_path):
    """No default tmux socket or agent panes are read in this integration check."""
    import shutil
    if not shutil.which("tmux"):
        pytest.skip("tmux unavailable")
    import os
    import tempfile
    directory = tempfile.TemporaryDirectory(prefix="stall-tmux-", dir="/private/tmp")
    socket = directory.name + "/s"
    env = {**os.environ, "HOME": str(tmp_path), "TMUX": ""}
    def tmux(*args):
        return subprocess.run(["tmux", "-f", "/dev/null", "-S", socket, *args], capture_output=True,
                              text=True, timeout=5, check=True, env=env)
    screen = tmp_path / "screen.txt"
    screen.write_text(CAPACITY)
    tmux("new-session", "-d", "-s", "FixtureCurie", "-x", "130", "-y", "20",
         "cat " + str(screen) + "; sleep 10")
    try:
        import time
        end = time.monotonic() + 2
        text = ""
        while time.monotonic() < end:
            text = tmux("capture-pane", "-p", "-J", "-t", "FixtureCurie").stdout
            if "Selected model" in text:
                break
            time.sleep(.02)
        assert stalls.classify(text)["state"] == "capacity"
    finally:
        tmux("kill-server")
        directory.cleanup()


def test_sampler_uses_shared_capture_and_session_parent(monkeypatch):
    from dashboard import server
    session = dict(created=101, session_id="$1", pane_pid=17)
    monkeypatch.setattr(server, "tmux_state", lambda: {"ChildCurie": session})
    monkeypatch.setattr(server, "agentmail_state", lambda: ({"ChildCurie": {"program": "codex"}}, {}))
    monkeypatch.setattr(server, "_process_tree_snapshot", lambda: {})
    monkeypatch.setattr(server, "_tmux_row_state", lambda *a: ("codex", "agent", True, None))
    monkeypatch.setattr(server, "_codex_registration", lambda name: dict(agent_id=2, project_key="/project"))
    monkeypatch.setattr(server, "_batched_registrations", lambda: None)
    monkeypatch.setattr(server, "_agent_runtime", lambda *a: {})
    monkeypatch.setattr(server, "_tmux", lambda argv: "PARENT_AGENT=ParentNewton\n")
    monkeypatch.setattr(server, "_provider_captures", {"ChildCurie": ("101:$1", 10, stalls.classify(CAPACITY))})
    rows = server._provider_stall_samples()
    assert rows == [sample(10)]


@pytest.mark.parametrize("parent", ["ParentNewton", "DifferentParent"])
def test_sender_checks_child_owner_and_parent_before_existing_mail_helper(tmp_path, monkeypatch, parent):
    from dashboard import server
    from types import SimpleNamespace
    row = sample(2)
    session = dict(created=101, session_id="$1", pane_pid=17)
    monkeypatch.setattr(server, "tmux_state", lambda: {"ChildCurie": session})
    monkeypatch.setattr(server, "_tmux", lambda a: "PARENT_AGENT=ParentNewton")
    monkeypatch.setattr(server, "_codex_registration", lambda n: dict(agent_id=2, project_key="/project"))
    monkeypatch.setattr(server, "_resume_registration", lambda *a: {"agent_id": 1})
    monkeypatch.setattr(server, "_runtime_agent_token", lambda n: "fixture-owner")
    monkeypatch.setattr(server, "_child_resume_module", lambda: SimpleNamespace(
        _load_state=lambda p: dict(parent_agent=parent, registration_token="fixture-owner"),
        _validate_identity=lambda *a, **k: None))
    calls = []
    monkeypatch.setattr(server, "_mcp_call", lambda *a, **k: calls.append((a, k)) or {"ok": True})
    event = dict(since=100, event_id="event-fixture", error=ERROR)
    status = server._provider_stall_send(row, event)
    if parent != row["parent"]:
        assert status == "parent_unverified"
        assert not calls
    else:
        assert status == "sent"
        assert calls[0][0][0] == "send_message"
        args = calls[0][0][1]
        assert args["sender_name"] == row["name"] and args["to"] == [row["parent"]]
        assert args["body_md"].startswith("telemetry の自動検知")
        assert "JST" in args["body_md"] and ERROR in args["body_md"]


def test_global_does_not_start_legacy_worker(monkeypatch):
    from dashboard import server
    monkeypatch.setattr(server, "GLOBAL_MODE", True)
    monkeypatch.setattr(server, "_provider_monitor", None)
    monkeypatch.setattr(server.threading, "Thread", lambda **kw: pytest.fail("legacy worker in global"))
    server._start_provider_stall_monitor()
    assert server._provider_monitor is None


def test_long_dashboard_outage_does_not_repeat_same_incident(tmp_path):
    sent = []
    path = tmp_path / "state.json"
    monitor = stalls.Monitor(path, lambda *args: sent.append(args) or "sent")
    monitor.tick([sample(1)], now=100)
    monitor.tick([sample(2)], now=105)
    monitor.tick([], now=100 + 10 * 86400)
    monitor = stalls.Monitor(path, lambda *args: sent.append(args) or "sent")
    monitor.tick([sample(3)], now=110 + 10 * 86400)
    monitor.tick([sample(4)], now=115 + 10 * 86400)
    assert len(sent) == 1


def test_legacy_worker_lifecycle_is_bound_to_dashboard(tmp_path, monkeypatch):
    from dashboard import server
    monkeypatch.setattr(server, "GLOBAL_MODE", False)
    monkeypatch.setattr(server, "RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_provider_monitor", None)
    monkeypatch.setattr(server, "_provider_thread", None)
    monkeypatch.setattr(server, "_provider_stop", threading.Event())
    monkeypatch.setattr(server, "_provider_stall_samples", lambda: [])
    server._start_provider_stall_monitor()
    first = server._provider_thread
    try:
        assert first.is_alive()
        server._start_provider_stall_monitor()
        assert server._provider_thread is first
    finally:
        server._stop_provider_stall_monitor()
    assert not first.is_alive()


def test_import_does_not_start_worker_with_an_inherited_runtime(tmp_path):
    import os
    from pathlib import Path
    import sys
    forbidden = tmp_path / "must-not-be-created"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTSTACK_")}
    env.update(HOME=str(tmp_path), AGENTSTACK_RUNTIME_DIR=str(forbidden),
               AGENTSTACK_MAIL_DB=str(tmp_path / "fixture.sqlite3"),
               AGENTSTACK_RUNTIME_MODE="legacy")
    code = "import dashboard.server as s; assert s._provider_monitor is None; assert s._provider_thread is None"
    subprocess.run([sys.executable, "-c", code], env=env,
                   cwd=Path(__file__).resolve().parents[1], timeout=10, check=True)
    assert not forbidden.exists()


def test_fixture_replaces_import_time_runtime(isolated_provider_stall_runtime):
    import os
    from dashboard import server
    assert server.RUNTIME_DIR == str(isolated_provider_stall_runtime)
    assert os.environ["AGENTSTACK_RUNTIME_DIR"] == server.RUNTIME_DIR
    assert "AGENTSTACK_CLIENT_CONFIG" not in os.environ
