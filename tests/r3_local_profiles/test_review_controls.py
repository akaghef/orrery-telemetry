"""Executed r1 mutant controls. These are NOT native/caller conformance runs."""
from copy import deepcopy
import json
import socket
import subprocess
import urllib.request

import pytest

from .evidence import (assert_auth_matrix, assert_bound_receipt, assert_caller_capture,
                       assert_intent_digest, assert_single_owner, assert_tree_unchanged,
                       assert_wire_rotation, capture_provider, credential_source,
                       existing_mutation_pending, intent_preimage, literal_intent,
                       owner_paths, snapshot_tree, write_private_json)
from .support import Canary, canonical_digest, fake_peer, post_fixture
from .test_instruments import request


def retrieve(source):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{source.server.server_port}/credential", timeout=2) as response:
        raw = response.read()
    if not source.current.matches(raw.decode(), "x-api-key"):
        pytest.fail("fixture credential transport mismatch", pytrace=False)


@pytest.mark.parametrize("mutation", [None, "all-unsupported", "no-helper", "false-success", "false-success-on-500"])
def test_auth_matrix_detects_no_native_success_or_unused_helper(canary, mutation):
    with credential_source(canary) as source:
        if mutation != "no-helper":
            retrieve(source)
        observations = []
        for header in ("bearer", "x-api-key"):
            with fake_peer("messages", canary, auth=header,
                           fault="500" if mutation == "false-success-on-500" else None) as peer:
                post_fixture(peer, request("messages"), None if mutation == "all-unsupported" else "bearer")
                success = peer.requests[0]["auth_ok"] or mutation == "false-success"
                observations.append((peer, {"cli_version": "fixture-version", "model_success": success,
                                            "reason": "LOCAL_MODEL_AUTH_HEADER_UNSUPPORTED"}))
        if mutation is None:
            assert assert_auth_matrix(observations, source) == ["bearer"]
        else:
            with pytest.raises(AssertionError):
                assert_auth_matrix(observations, source)


@pytest.mark.parametrize("mutation", [None, "stale-token", "no-refresh-helper", "no-initial-helper"])
def test_wire_rotation_detects_old_token_reuse_despite_success_claim(canary, mutation):
    rotated = Canary()
    with credential_source(canary) as source, fake_peer("messages", canary, auth="either") as peer:
        if mutation != "no-initial-helper":
            retrieve(source)
        assert post_fixture(peer, request("messages"), "x-api-key")[0] == 200
        source.rotate(rotated)
        peer.rotate_credential(rotated)
        if mutation != "no-refresh-helper":
            retrieve(source)
        if mutation == "stale-token":
            # Construct a real stale HTTP request; never write/log the canary.
            headers = {"Content-Type": "application/json", "Anthropic-Version": "2023-06-01",
                       "X-Api-Key": canary.header("x-api-key")}
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(peer.base_url + "/v1/messages", json.dumps(request("messages")).encode(), headers)
            with pytest.raises(urllib.error.HTTPError):
                opener.open(req, timeout=2)
        else:
            assert post_fixture(peer, request("messages"), "x-api-key")[0] == 200
        if mutation is None:
            assert_wire_rotation(peer, source)
        else:
            with pytest.raises(AssertionError):
                assert_wire_rotation(peer, source)


def test_compound_bash_first_approved_side_effect_is_detected(workspace):
    outside = workspace.tmp / "outside"
    outside.mkdir()
    last = outside / "last.txt"
    first = workspace.scratch.parent / "first.txt"
    first.write_text("unchanged\n")
    last.write_text("unchanged\n")
    before_first, before_last = snapshot_tree(first.parent), snapshot_tree(outside)
    assert_tree_unchanged(first.parent, before_first)
    # Wrong admission executes only the approved first component, then rejects
    # the second. Outside file and unapproved_writes=[] cannot detect this.
    first.write_text("wrongly executed first command\n")
    assert_tree_unchanged(outside, before_last)
    with pytest.raises(AssertionError, match="protected file tree changed"):
        assert_tree_unchanged(first.parent, before_first)


@pytest.mark.parametrize("mutation", ["owner", "receipt", "pending", "shadow-profile", "shadow-launch", "shadow-cache"])
def test_persisted_intent_oracle_detects_dict_preserving_overwrite(workspace, mutation):
    base = workspace.tmp / "client"
    owner, receipt, pending = owner_paths(base, "global")
    old = literal_intent(workspace)
    write_private_json(owner, {"launch_intent": old})
    write_private_json(receipt, {"phase": "exec-unknown", "intent_digest": canonical_digest(old)})
    write_private_json(pending, {"request_id": "00000000-0000-4000-8000-000000000042", "request_hash": canonical_digest(old)})
    before = snapshot_tree(base)
    assert_single_owner(base, owner)
    saved_dict = deepcopy(old)
    def wrong_revision(value):
        new = deepcopy(value)
        new["revision"] += 1
        if mutation == "owner":
            write_private_json(owner, {"launch_intent": new})
        elif mutation == "receipt":
            write_private_json(receipt, {"phase": "planned", "intent_digest": canonical_digest(new)})
        elif mutation == "pending":
            write_private_json(pending, {"request_id": "replaced", "request_hash": canonical_digest(new)})
        else:
            path = {"shadow-profile": base / "runtime/child-state.json",
                    "shadow-launch": receipt, "shadow-cache": base / "unexpected/cache.json"}[mutation]
            write_private_json(path, {"launch_intent": value})
        return new
    wrong_revision(old)
    assert old == saved_dict  # The old r0 check still passes.
    with pytest.raises(AssertionError):
        assert_tree_unchanged(base, before)
    if mutation.startswith("shadow"):
        with pytest.raises(AssertionError, match="shadow owner"):
            assert_single_owner(base, owner)


@pytest.mark.parametrize("mutation", ["constant", "omit-auth", "omit-scope", "wrong-profile"])
def test_independent_intent_digest_rejects_mutant_preimage(workspace, mutation):
    intent = literal_intent(workspace)
    correct = canonical_digest(intent_preimage(intent))
    assert_intent_digest(intent, correct)
    assert_bound_receipt(intent, {"intent_digest": correct})
    preimage = deepcopy(intent_preimage(intent))
    if mutation == "constant":
        wrong = "0" * 64
    else:
        if mutation == "omit-auth":
            preimage["model"]["resolved"].pop("auth")
        elif mutation == "omit-scope":
            preimage.pop("effective_scope")
        else:
            preimage["model"]["profile_sha256"] = "b" * 64
        wrong = canonical_digest(preimage)
    for check in (lambda: assert_intent_digest(intent, wrong),
                  lambda: assert_bound_receipt(intent, {"intent_digest": wrong})):
        with pytest.raises(AssertionError):
            check()


def test_single_caller_bypass_is_detected_by_real_capture_binary(workspace):
    capture = workspace.tmp / "capture.json"
    binary = capture_provider(workspace.tmp / "bin/capture-provider", capture)
    env = {"HOME": str(workspace.tmp / "home"), "PATH": "/usr/bin:/bin"}
    cwd = workspace.scratch.parent
    subprocess.run([str(binary), "--model", "fixture-glm-model"], cwd=cwd, env=env, check=True)
    expected = json.loads(capture.read_text())
    assert_caller_capture(capture, expected)
    # Wrong caller independently assembles catalog argv, bypassing the resolver.
    subprocess.run([str(binary), "--model", "gpt-fake"], cwd=cwd, env=env, check=True)
    with pytest.raises(AssertionError, match="caller bypass"):
        assert_caller_capture(capture, expected)


def test_pending_seed_uses_existing_wire_contract():
    row = existing_mutation_pending()
    assert row["request_hash"] == canonical_digest(row["canonical_payload"])
    assert row["canonical_payload"]["arguments"]["task_description"] == "fixture"
    assert "registration_token" not in row["canonical_payload"]["arguments"]


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_only_live_fixture_socket_addresses_are_allowed(canary, method):
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="non-fixture network"):
            getattr(sock, method)(("127.0.0.1", 1))
    with fake_peer("responses", canary) as peer:
        address = ("127.0.0.1", peer._server.server_port)
        assert post_fixture(peer, request("responses"))[0] == 200
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="non-fixture network"):
            getattr(sock, method)(address)


@pytest.mark.parametrize("fault", ["untrusted-ca", "wrong-host"])
def test_tls_rejection_occurs_before_credential_request(canary, tmp_path, fault):
    ip = "127.0.0.2" if fault == "wrong-host" else "127.0.0.1"
    with fake_peer("responses", canary, tls_dir=tmp_path / "tls", cert_ip=ip) as peer:
        if fault == "wrong-host":
            with pytest.raises(urllib.error.URLError):
                post_fixture(peer, request("responses"))
        else:
            # Deliberately omit the fixture CA, never disable verification.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with pytest.raises(urllib.error.URLError):
                opener.open(peer.base_url + "/v1/responses", timeout=2)
        assert peer.requests == []
