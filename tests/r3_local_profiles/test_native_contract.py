"""Red native acceptance specification, NOT synthetic CLI acceptance evidence.

The deliberately missing native_acceptance test adapter must observe real
pinned CLIs and existing hooks/receipt paths, with isolated transports. Do not
make these green by implementing a provider or permission engine in test code.
The fake-peer instruments themselves have separate green negative controls.
"""
import pytest

from .support import (MAIL, MODEL, assert_delegation, assert_denial, assert_failure,
                      assert_roundtrip, assert_same_launch, fake_peer)
from .test_instruments import SURFACES


def observed_native(trace, provider):
    assert trace["provider"] == provider
    assert trace["cli_kind"] == "native", "fake CLI cannot establish native compatibility"
    assert trace["cli_version"], "native CLI version missing"
    assert trace["observations_complete"], "missing process/wire observation"
    assert trace["real_services_used"] == []


@pytest.mark.parametrize("provider,protocol", [("codex", "responses"), ("claude", "messages")])
def test_native_function_roundtrip_model_path_and_both_turn_tools(native_probe, workspace, canary, provider, protocol):
    # No authentication in the HTTP loopback fixture. Authenticated native
    # probes below use verified fixture TLS, not a weakened URL policy.
    with fake_peer(protocol, canary) as peer:
        trace = native_probe("function-roundtrip", workspace=workspace,
                             provider=provider, peer=peer, canary=canary)
        observed_native(trace, provider)
        assert_roundtrip(peer)
        assert all(row["body"]["model"] == MODEL for row in peer.requests)
        assert trace["tool_effects"] == ["echo:fixture"]
        assert trace["model_success"]


@pytest.mark.parametrize("provider,protocol,header", [
    ("codex", "responses", "bearer"),
    ("codex", "responses", "x-api-key"),
    ("claude", "messages", "bearer"),
    ("claude", "messages", "x-api-key"),
])
def test_native_header_is_observed_not_assumed(native_probe, workspace, canary, provider, protocol, header):
    with fake_peer(protocol, canary, auth=header, tls_dir=workspace.tmp / "peer-tls") as peer:
        trace = native_probe("auth-header", workspace=workspace, provider=provider,
                             peer=peer, canary=canary, fake_credential_only=True)
        observed_native(trace, provider)
        assert peer.requests, "native helper never reached fake peer"
        if provider == "codex":
            assert peer.requests[0]["auth_form"] == "bearer"
        # At least the actual native shape must work; an incompatible endpoint
        # must be rejected, not 'fixed' with a token-valued env or header config.
        if peer.requests[0]["auth_form"] == header:
            assert trace["model_success"]
            assert all(row["auth_ok"] for row in peer.requests)
            if provider == "codex":
                assert header == "bearer"
        else:
            assert_failure(trace, "LOCAL_MODEL_AUTH_HEADER_UNSUPPORTED", pre_exec=False)
        assert trace["auth_mechanism"] == ("command-backed-auth" if provider == "codex" else "apiKeyHelper")
        assert set(SURFACES) <= set(trace["surfaces"]), "canary observation surface omitted"
        canary.scan(trace["surfaces"])


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_native_refresh_rotation_without_token_in_any_process_env(native_probe, workspace, canary, provider):
    trace = native_probe("auth-refresh", workspace=workspace, provider=provider,
                         canary=canary, fake_clock=True, production_ttl_ms=300000)
    observed_native(trace, provider)
    assert trace["helper_calls"] >= 2 and trace["rotated_token_accepted"]
    assert trace["same_intent_digest"]
    assert trace["production_ttl_ms"] == 300000
    assert set(SURFACES) <= set(trace["surfaces"])
    canary.scan(trace["surfaces"])
    assert trace["rotated_canary_scan_passed"]


@pytest.mark.parametrize("fault,reason", [
    ("auth-timeout", "LOCAL_MODEL_AUTH_FAILED"),
    ("auth-exit", "LOCAL_MODEL_AUTH_FAILED"),
    ("401", "LOCAL_MODEL_AUTH_FAILED"),
    ("500", "LOCAL_MODEL_REQUEST_FAILED"),
    ("disconnect", "LOCAL_MODEL_REQUEST_FAILED"),
    ("redirect", "LOCAL_MODEL_REQUEST_FAILED"),
    ("unreachable", "LOCAL_MODEL_REQUEST_FAILED"),
    ("invalid-tool-json", "LOCAL_MODEL_PROTOCOL_FAILED"),
])
@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_native_failure_never_falls_back_or_loses_exec_receipt(native_probe, workspace, canary, provider, fault, reason):
    trace = native_probe("request-failure", workspace=workspace, provider=provider,
                         fault=fault, canary=canary)
    observed_native(trace, provider)
    assert_failure(trace, reason, pre_exec=False)
    assert "runtime_ready" in trace  # readiness and model success are distinct facts
    assert set(SURFACES) <= set(trace["surfaces"])
    canary.scan(trace["surfaces"])


@pytest.mark.parametrize("task_source", ["temporary-task", "vault-handoff"])
def test_claude_external_task_reserve_edit_renew_release_report(native_probe, workspace, canary, task_source):
    trace = native_probe("delegation", workspace=workspace, provider="claude",
                         task_source=task_source, canary=canary,
                         mail_tools=list(MAIL), compound_bash="approved")
    observed_native(trace, "claude")
    assert_delegation(trace, workspace.scope, workspace.scratch)
    assert workspace.scratch.read_text() == "after\n"
    assert trace["compound_bash_effects"] == ["approved-cwd", "approved-pipe"]
    assert trace["external_task_read"] == str(workspace.task if task_source == "temporary-task" else workspace.handoff)


@pytest.mark.parametrize("operation", ["outside-read", "approved-first-outside-second", "outside-pipe", "outside-redirect"])
def test_claude_scope_denial_has_no_partial_effect_and_reports(native_probe, workspace, canary, operation):
    outside = workspace.tmp / "outside"
    outside.mkdir()
    sentinel = outside / "denied.md"
    sentinel.write_text("do not read or edit\n")
    trace = native_probe("scope-denial", workspace=workspace, provider="claude",
                         operation=operation, outside=outside, canary=canary)
    observed_native(trace, "claude")
    assert_denial(trace)
    assert sentinel.read_text() == "do not read or edit\n"
    assert sorted(p.name for p in outside.iterdir()) == ["denied.md"]


@pytest.mark.parametrize("conflict", ["inherited-Bash-allow", "sandbox-auto-allow", "managed-ask-mail", "missing-reservation-tool", "unverifiable-plugin"])
def test_claude_unapproved_effective_scope_rejected_before_exec(native_probe, workspace, canary, conflict):
    before = workspace.settings.read_bytes()
    trace = native_probe("scope-preflight", workspace=workspace, provider="claude",
                         conflict=conflict, canary=canary)
    assert_failure(trace, "LOCAL_MODEL_PERMISSION_PLAN_UNSATISFIABLE", pre_exec=True)
    assert workspace.settings.read_bytes() == before


def test_native_replay_interruption_and_no_shadow_intent(native_probe, workspace, canary):
    trace = native_probe("lifecycle", workspace=workspace, provider="codex", canary=canary,
                         operations=["spawn", "end", "resume", "headless-wake"],
                         interrupt_at=["intent-saved", "launch-planned", "exec-observed", "receipt-pending"])
    observed_native(trace, "codex")
    assert_same_launch(trace["launches"])
    assert trace["intent_owner_count"] == 1
    assert trace["unknown_outcome_requires_operator"]
    assert trace["pending_intent_overwritten"] is False
    assert trace["caller_fields_dropped"] == []
    canary.scan(trace["surfaces"])


def test_builtin_model_metadata_collision_is_rejected_not_renamed(native_probe, workspace, canary):
    trace = native_probe("model-metadata-collision", workspace=workspace,
                         provider="codex", canary=canary,
                         model_id="gpt-fixture-collision", metadata_override_supported=False,
                         metadata_tool_mode="code_mode")
    assert_failure(trace, "LOCAL_MODEL_ADAPTER_UNSUPPORTED", pre_exec=True)
    assert trace["renamed_model"] is False


def test_auth_lookalike_in_caller_cwd_is_never_executed(native_probe, workspace, canary):
    trace = native_probe("auth-cwd-lookalike", workspace=workspace, provider="claude",
                         canary=canary, operations=["spawn", "resume", "headless-wake"])
    observed_native(trace, "claude")
    assert trace["lookalike_executions"] == 0
    assert trace["auth_contexts"] == [workspace.context["auth_context"]] * 3
    assert set(SURFACES) <= set(trace["surfaces"])
    canary.scan(trace["surfaces"])
