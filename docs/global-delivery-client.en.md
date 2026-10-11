# Isolated global client launch and delivery

[日本語](global-delivery-client.md)

Legacy mode remains the default and global activation remains false. Use a temporary HOME and a freshly prepared isolated server candidate. Live cutover and import of released legacy formats remain outside this change.

## Authorities and layout

The existing context and credential own authentication and binding. Window identity lives only in context.identity. Old extra window fields in global credentials/profiles/session receipts are ignored without rewriting on read and omitted on the next regular write. Existing live-sessions and session_index own process and provider-session evidence. runtime/launch.json contains only launch-specific intent and resolution.

The server notification fact is the completion authority. Local delivery intent records an interrupted external effect and retry schedule, never a delivered cache. Inbox reads do not consume notifications. Explicit read, acknowledgement and delivery completion do.

The consumer root is agentstack_home/global-consumer/SHA256(mail_instance_id)/agents/agent_id. The existing server-config.json must match isolation, instance, candidate generation, epoch, runtime_root, authority and lock. Mismatch fails before output with CONSUMER_ISOLATION_MISMATCH. This corrects the authority-derived root in wire v3: client state is outside the server preparation workspace. All clients of one instance share the same root. Agent and App external routes contain only client_name, without tokens, display names or a current launch copy.

## Launch recovery

Standalone and child launch/resume use the same helper. Output slots, command, cwd and server capabilities are checked before window binding and execution. Registration and launch are distinct operations; an uncertain launch is never retried by registering another identity.

| Phase | Exit |
| --- | --- |
| planned / gated | launch-retry or launch-cancel with the current status digest |
| exec-unknown | Explicit launch-resolve: abandon, confirmed-running or confirmed-ended |
| running | runtime_ready requires trusted SessionStart and remote owner/window verification |
| ended / cancelled | Prepare a new explicit launch after all confirmed-ended live writes finish |
| abandoned | Leave the unknown process alone; resolve explicitly when evidence is available |

Pass JSON on stdin to `agentstack-runtime-client operator-json OPERATION`. The source schema is [local_operator_wire.operations](../packages/agentstack_mail/fixtures/global-client-4c.json). `launch-cancel`, `launch-resolve` and `resolve-delivery` require `confirm:true`; `launch-retry` does not accept confirm.

Set client_root to the fixed client root and AGENTSTACK_CLIENT_CONFIG to its runtime-client.json. Read launch ID from the existing launch.json and message ID from doctor-local or the received notification. Read current status first:
```sh
# AGENTSTACK_CLIENT_CONFIG points to this client's runtime-client.json.
launch_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["launch_id"])' "$client_root/runtime/launch.json")
status_file=$(mktemp)
agentstack-runtime-client operator-json launch-status <<EOF_JSON > "$status_file"
{"launch_id":"$launch_id"}
EOF_JSON
state_sha256=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state_sha256"])' "$status_file")
cat "$status_file"
```

Inspect phase, operator_required, next_operations and remote_owner_verified. state_sha256 covers current context, credential, launch, live sessions and intent under the same lock. On RESOLUTION_DIGEST_MISMATCH, read status again and reconsider the action.

```sh
# exec-unknown / abandoned: acknowledge uncertainty; never claims completion.
agentstack-runtime-client operator-json launch-resolve <<EOF_JSON
{"launch_id":"$launch_id","expected_state_sha256":"$state_sha256","confirm":true,"resolution":{"action":"abandon","operator_evidence":"The process could not be independently verified"}}
EOF_JSON
```

```sh
# planned / gated only: select ONE operation after reading a fresh status.
agentstack-runtime-client operator-json launch-cancel <<EOF_JSON
{"launch_id":"$launch_id","expected_state_sha256":"$state_sha256","confirm":true}
EOF_JSON
# Alternatively retry (no confirm field in its schema):
agentstack-runtime-client operator-json launch-retry <<EOF_JSON
{"launch_id":"$launch_id","expected_state_sha256":"$state_sha256"}
EOF_JSON
```

```sh
# Use the message_id reported by doctor-local (42 is an example).
agentstack-runtime-client operator-json delivery-status <<EOF_JSON
{"message_id":42}
EOF_JSON
```

Read status again after the operation. Abandon returns phase=abandoned/runtime_ready=false; cancel returns phase=cancelled/prepared=true/runtime_ready=false; retry returns phase=gated/prepared=true and is not ready until trusted SessionStart. For delivery, obtain the delivery-status digest and pass message_id, expected_state_sha256, confirm:true and resolution to resolve-delivery. Actions are retry-at-least-once, confirm-external-completed and abandon-without-consuming; operator_evidence records the basis for the decision. Abandon-without-consuming only blocks locally, without consuming unread state or the server notification fact.

If confirmed-ended is interrupted while saving live sessions, status returns operator_required=true and next_operations=[launch-resolve]. Resubmit the same confirmed-ended with a fresh digest to finish the remaining writes before a new launch. Local status and abandon also work while Mail is unreachable.

The bound proxy derives server schemas by removing owner and binding inputs. Local bootstrap verifies the trusted receipt; runtime_status reports the bound runtime. Caller tokens and display names never select another owner. New windows use the existing agent_id foreign key and NULL legacy_project_id.

## Codex hook trust

Claude mcp.json and orrery-hooks.json are derived files regenerated under the launch gate operation mutex. Hooks use `--settings orrery-hooks.json`; the provider-owned settings.json model and permissions stay untouched. Gemini keeps its native `.gemini/config/mcp_config.json` as the sole configuration, CAS-updating only the `orrery-mail` entry under the same mutex and preserving other top-level keys and MCP entries. Codex uses per-call `-c` overrides without generating its default config. Concurrent edits return `GENERATED_CONFIG_CHANGED`, unreadable files return `GENERATED_CONFIG_UNREADABLE`, and invalid Gemini JSON or structure returns `GENERATED_CONFIG_JSON_INVALID` without overwriting bytes. Correct private paths/permissions or JSON, then retry explicitly. There is no separate configuration ledger.

Normal launch never approves trust. The approved plan in the fixed provider HOME must match plugin payload and native config trust. Otherwise launch fails with `CODEX_HOOK_CONFIGURE_REQUIRED`. Approval cannot be reused for changed payload.

With `AGENTSTACK_CLIENT_CONFIG` pointing to the intended fixed context, the operator prepares a plan:

```sh
agentstack-runtime-client codex-plan operator > hook-plan.review.json
```

A human reviews provider HOME, plugin placement and hook definitions. Only after approving them, the human changes `approved` to `true` in the JSON and supplies it on stdin:

```sh
agentstack-runtime-client codex-apply operator < hook-plan.review.json
```

This reuses existing plan / apply_trust code. Launch, update and resume never grant approval. Arbitrary HOME/placement and installing another binary version are unsupported. The plugin's Mail MCP is disabled for this process; the supplied common-client proxy runs while approved hooks remain active.

Gemini uses the fixed provider HOME's `.gemini/config/mcp_config.json` and existing stream renderer. Headless children accept session evidence only from provider `init/conversation_id`. Missing init fails with `GEMINI_SESSION_RECEIPT_UNAVAILABLE`; interactive roots fail with `GEMINI_INTERACTIVE_RUNTIME_UNSUPPORTED`. Neither becomes ready. Gemini is tested with a synthetic stream-provider fixture only; real agy remains unverified.

## Operator configuration for persistent execution

The fixed profile.json stores one typed execution object. It adds no owner/binding copies. Regular run only reads it. Released persistent profiles remain LEGACY_FILE_REQUIRES_IMPORT; explicit import belongs to PR7.

```sh
agentstack-persistent configure --profile "$client_root/profile.json" --operator <<EOF_JSON
{"argv":["/absolute/python","/absolute/bot.py"],"interaction":"headless","cwd":"/absolute/private-workdir"}
EOF_JSON
agentstack-persistent run --profile "$client_root/profile.json"
```

argv must be an array, never a shell command string. cwd must be private, within the same isolation root, with no symlink components. Run without execution returns PERSISTENT_EXECUTION_REQUIRED. Arbitrary profile destinations and concurrent reads of legacy profiles remain unsupported.

The bot uses AGENTSTACK_CLIENT_CONFIG and the common client. After recording trusted SessionStart, it receives the existing orrery-mail-notification-reply-v1 protocol on the fixed AGENTSTACK_PERSISTENT_WAKE_SOCKET. Requests include the message and a stable reply_request_id. Complete reply_message using that UUID before returning status:replied with the original message_id. An interrupted or uncertain reply does not complete delivery.

## Delivery and health

Hints carry IDs and binding only. Owner pending-only/exact-ID reads select actual messages. Purged, read, acknowledged and imported-consumed messages are never reinjected. One formatter produces complete-body, preview and no-body notices.

| Channel | Retry bounds |
| --- | --- |
| tmux / standalone hook / persistent / daemon | Existing 30-second cooldown; no attempt cap; unknown waits for both channel timeout plus 30-second lease and backoff |
| Codex App | 0.25-second poll; 2-second coalesce; 12 wakes/hour; 900-second lease; 2..300-second backoff; 5 attempts |

The watcher waits with the existing two-second poll when the server-owned signals directory is absent, without creating it. Numeric intent names are candidates only; per-message locking validates existence, type and permissions. Diagnostics report unsafe slots without stopping other messages. BELOW_MIN_IMPORTANCE and SELF_MESSAGE are policy decisions, not operator incidents; existing reconciliation removes these intents after their hint disappears through read/ack or consumption. Bare shells, capture timeouts, absent sessions, disabled Mail and unresolved routes precede handoff and consume no attempt. App rate/attempt limits block without dead-lettering. Unknown retry reports duplicate_possible. Once confirmed, retry only resends server completion. Late attempt, old credential-generation and old-launch callbacks fail closed.

Top-level server status describes request readiness; signal.status describes notification delivery health. Existing doctor warnings and local status/worker logs report unknown or blocked work. Dashboard delivery-health UI remains later work.

For an unsafe slot, `repair-local-state` is a manual instruction, not a CLI operation or automatic repair. Correct the reported message slot type/permissions, leave healthy slots untouched, never follow a symlink, and rerun diagnostics.

## Lock order and tests

Acquire agent directory A, client operation C, message directory M, mutation U, authority F in that order, using only required locks. Release all locks before provider execution, tmux/bot waits and hook stdout flush. No launch.lock or delivery database is added. Clients never open, close, copy or hash SQLite main/WAL/SHM/journal files.

Tests wait for real HTTP health and use temporary HOME and a dedicated tmux socket. Installer payloads and synthetic layouts derive from the installer's single declaration. Report related, outside-sandbox and full-suite results separately.

Only the management delivery_completed callback can record a completed handoff after recipient retirement, with the existing recipient and exact credential generation still checked. Public tools continue to reject retired recipients, and reactivation preserves the completion fact.

For task_delivery=mail, a child task is sent using the parent’s existing mutation intent and the same task-digest-derived UUID on retries. Task bytes are not embedded in launch arguments; the child fetches its inbox. Ordinary contact policy applies without automatic approval. tools_selection uses the existing child_tools policy against fixed provider HOME configuration; missing selected servers fail with TOOL_SELECTION_UNAVAILABLE.

### Repeating an explicit Codex plan

If `codex-plan` stops after building the marketplace, the operator explicitly repeats the same command. Only the fixed provider HOME's `marketplace/` is rebuilt. Every replan returns an unapproved plan, even for identical content: one rule prevents an old approval from authorizing different content. Ordinary launch neither rebuilds nor approves anything.

### App cold-wake boundary

The App history HOME uses the same shared resolver as the legacy consumer: `CODEX_HOME`, or the ordinary `~/.codex` when absent. History is never copied or moved into a child root; only the resume process writes to it. Resume receives the target child's context-bound proxy, with one concurrent resume per root. It uses the existing plugin and human-approved native trust without automatic approval. Ordinary child launch still uses that child's fixed provider HOME.

App cold wake is tested with fixtures only; actual resume is unverified. At 12 wakes per hour, admission becomes `blocked / WAKE_RATE_LIMITED` without incrementing handoff attempts. Once the time window permits, the same intent resumes. The server notification fact remains pending; polling never consumes it.

App uses the same execution settings reader as the legacy consumer for `AGENTSTACK_CODEX_APP_COLD_WAKE`, `AGENTSTACK_CODEX_BINARY`, `AGENTSTACK_CODEX_APP_PLUGIN_ID`, and `AGENTSTACK_CODEX_APP_SKIP_GIT_CHECK`. Explicit cold-wake disablement and Mail disabled prevent new handoffs.

Global watcher reacts to signal-directory events, falling back to the legacy two-second poll; the existing scan interval rescues missed events. Legacy watcher numeric declarations remain the sole source for cooldown, headless timeout and poll/scan defaults. App timing uses the same AGENTSTACK_CODEX_APP_WAKE_* reader and WakePolicy defaults as the legacy bridge. Waiting/blocked decisions precede fetch; below-minimum importance is cached in the same intent and reconsidered when its threshold changes. A prior unknown delivery keeps duplicate_possible even if a later attempt is known not to have been submitted. A previous launch for the same owner/binding is replanned; a genuine owner/credential change blocks only that message and can be explicitly rebound by retry-at-least-once. Errors in one message or App root do not stop the others.
