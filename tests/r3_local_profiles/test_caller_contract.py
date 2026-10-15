"""Actual-caller integration specifications; source labels are not evidence.

The missing caller_acceptance TEST adapter must call each named real entry
and use existing isolated client/Mail fixtures. It may replace external commands
with capture binaries, but never replace the entry/parser/resolver under test.
"""
import json

import pytest

from .evidence import (CALLERS, assert_caller_capture, assert_single_owner,
                       assert_tree_unchanged, capture_provider, owner_paths, snapshot_tree)


def test_real_callers_preserve_literal_intent_and_same_executed_argv_env(caller_probe, workspace):
    capture = workspace.tmp / "captured-provider.json"
    binary = capture_provider(workspace.tmp / "bin/fixture-provider", capture)
    owner, _, _ = owner_paths(workspace.tmp / "client", "global")
    captures, intents = [], []
    for entry in CALLERS:
        # Each binding reconstructs the same isolated client/name/context and
        # invokes the real entry. It must NOT synthesize the capture/intent or
        # replace the production entry/parser/resolver. All external commands
        # are fixture commands, including transport/tmux (no native CLI needed).
        caller_probe(entry, workspace=workspace, real_entry=CALLERS[entry],
                     provider_binary=binary, owner=owner,
                     prepare_only=(entry == "operator"), fresh_same_context=True,
                     provider_session_id="native-fixture-session")
        saved = json.loads(owner.read_text())["launch_intent"]
        assert saved["model"]["ref"] == "local:fixture"
        assert saved["model"]["resolved"]["model"] == "fixture-glm-model"
        for field in ("cwd", "task", "name", "tools", "base", "resources", "detached"):
            assert saved[field] == workspace.caller[field]
        execution = json.loads(capture.read_text())
        argv = execution["argv"]
        models = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--model=")]
        models += [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg in ("--model", "-m")]
        assert models == ["fixture-glm-model"]
        assert execution["cwd"] == str(workspace.scratch.parent)
        assert execution["env"]["HOME"] == str(workspace.tmp / "client/runtime/provider-home/codex")
        if captures:
            if entry == "resume-child":
                # Native resume has an intentionally different command envelope.
                # Do not demand that resume silently become a fresh spawn.
                assert "resume" in argv and "native-fixture-session" in argv
                assert execution["env"] == captures[0]["env"]
                assert execution["cwd"] == captures[0]["cwd"]
                def overrides(args):
                    return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-c"]
                assert overrides(argv) == overrides(captures[0]["argv"])
            else:
                assert_caller_capture(capture, captures[0])
            assert saved == intents[0]
        captures.append(execution)
        intents.append(saved)
        assert_single_owner(workspace.tmp / "client", owner)


@pytest.mark.parametrize("stage", ["intent-saved", "launch-planned", "exec-observed", "receipt-pending"])
def test_actual_client_reconstruction_replays_old_pending_without_revision(caller_probe, workspace, stage):
    base = workspace.tmp / "client"
    before = []
    def pending_saved():
        before.append(snapshot_tree(base))
    caller_probe("pending-replay", workspace=workspace, interrupt_at=stage,
                 client_root=base, on_pending_saved=pending_saved,
                 reconstruct_runtime_client=True, attempt_new_revision=True,
                 hold_receipt_pending=True,
                 unknown_requires_operator=stage in ("exec-observed", "receipt-pending"))
    assert len(before) == 1, "actual pending-save boundary not exercised"
    assert_tree_unchanged(base, before[0])
    owner, _, _ = owner_paths(base, "global")
    assert_single_owner(base, owner)
