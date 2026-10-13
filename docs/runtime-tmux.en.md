# Shared boundary for tmux consumers

This draft shares legacy transport resolution and target protection. It adds no public Mail tool or dashboard API. Legacy remains the default and activation remains false. New global launches on a dedicated server are not enabled yet; global cockpit terminal/protocol migration remains a later step.

## CLI and schema

The entry is the existing `agentstack-runtime-client`. `bin/lib/runtime_tmux_contract.json` defines the JSON contract, validated by the existing `schema_contract.py`.

| Command | Input | Output / use |
|---|---|---|
| `tmux-capabilities` | None | `contract=orrery-tmux-target-v1` and `legacy-transport` / `legacy-target-admission`. Do not infer capabilities from a version string |
| `tmux-legacy-transport` | None | Verified socket, tmux argv prefix, server PID and environment removals, pinned at consumer startup |
| `tmux-legacy-target` | `--session NAME` or `--pane %N`, and `--operation capture\|attach\|input\|split\|close\|kill-session` | Admitted panes/PIDs/start times and a digest. Refusals return exit2 and a fixed reason; the consumer performs no side effect on that target |

A default legacy startup without a server returns only its destination and a null server_pid. It creates no socket, server or mutex; target actions remain unavailable until an actual server is verified.

All commands are read-only. They accept no arbitrary socket/root argument, token or substitute for owner authorization. `environment_unset` is a delta removing `TMUX` and `TMUX_PANE`; output never contains inherited credentials or the full environment. Global owner target/attach/compact/end require separate authorization and are not provided by this capability.

Consumers pin the socket/server PID at startup and repeat admission on that transport immediately before each operation. Append operations to the returned argv prefix and apply the environment removals. Retain explicit `-S`; never return to bare name targeting. Session destruction checks every constituent pane. A pane number or name prefix alone does not prove membership.

## Legacy endpoint and target protection

The default endpoint follows `TMUX_TMPDIR` and UID. An inherited custom `TMUX` must match a UID-owned socket in a private directory and the actual server PID. A new global fixed-directory endpoint is refused as a legacy endpoint with `GLOBAL_TMUX_LEGACY_CONSUMER_REFUSED`. Standard tmux0660 sockets are accepted only beneath a same-UID0700 parent that prevents traversal by others. Existing sockets are never chmodded.

The global endpoint derivation hashes canonical client home and Mail instance into a short absolute path. Epoch, TMPDIR and deep HOME do not change it. There is no socket registry, and launch does not use this function at this stage.

Legacy protection reads existing context/credential/launch/live truth under fixed `clients/` using the existing read-only snapshot entry. It creates no mutex, launch, relationship ledger or SQLite handle. A live global pane matching the current pane/PID/start yields `LEGACY_TARGET_IS_GLOBAL`. A damaged, partial or unverified root refuses that target operation. Missing/older helpers use the consumer's fixed-layout negative admission; only a proven pure legacy installation retains the previous behavior.

Do not consume a refused notification as successfully delivered: retain its existing reason/retry path and continue other normal legacy targets. Do not stop harmless roster reads globally. Check the fixed root/config and retry the same operation; never borrow another receipt or adopt an unknown target by name.

Socket, pane membership and PID/start are checked before and after admission. Admission and an external tmux side effect are not one atomic OS transaction. This is not an isolation boundary against a noncooperating same-UID process replacing targets. Consumers revalidate without an intervening wait and refuse changed targets.

## Implementation stages

Telemetry transport/consumers and public cockpit legacy protection use separate PRs. This draft fixes their shared CLI, and cockpit fixtures deploy its actual code through the installer payload declaration. Dedicated launch is not enabled before both merges and launcher deployment support. No rollout flag or verification ledger is added.

Tests use temporary HOME, short private directories and isolated `-L` servers. The execution boundary pins the verified absolute `-S` socket. Tests never use the live default tmux server, real provider HOME or real Mail DB.
