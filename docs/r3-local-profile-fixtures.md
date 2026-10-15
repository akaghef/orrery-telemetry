# R3 local-model profile: red acceptance fixtures / 実装前の受入 fixture

2026-10-15。承認済みの設計 v3 §§1–7 を対象とする **tests-only の draft**。
製品の local profile 対応を追加・有効化する変更ではない。R2-Ta #263 と
R2-Tb の後に接続する契約を先にレビューする。GLM は Responses/Messages の
互換性を条件とし、Chat Completions の変換 gateway は含めない。

This draft specifies tests before implementation, not working local-model support.
It must remain red until R3 is wired to the existing common launch/receipt/tool
plan paths. No product code, default model selection, live services, trust, or
user configuration changes are part of this patch.

## ケースと green の意味 / Case map

| Fixture | 設計・既存 caller 表 | 実装後に確認できること / Green means |
| --- | --- | --- |
| `test_profile_contract.py` | §§1–2, 6–7; V3/V6 | literal schema、固定 root・UID・mode・regular file・symlink/hardlink/unsafe parent、名前、adapter/env/URL 制限。無視せず拒否。全 adapter/argv を含む canonical digest、TOML 表記の不変性。 |
| `test_intent_contract.py` | §§1, 3, 6–7; V3/V6, A12; L6/7/10/13/67, L33/71/73 | caller→同じ保存 intent→spawn/resume/wake の実行投影。global は profile.json、legacy は child-state の一つの owner。launch receipt は digest 参照のみ。prepare/exec 間の差替え、設定/runner/CLI/権限の drift を副作用前に拒否。 |
| `test_auth_contract.py` | §4, §7 | 偽 credential executable を executor seam で観測。絶対 argv・固定 cwd・空からの最小 env・5秒・8KiB・shell 無し。異常 stdout/exit/timeout は秘密無しの固定 reason。token rotation は profile/intent revision ではない。 |
| `test_native_contract.py` | §§3–7; V3/V6 | 偽 Responses/Messages peer に **実 CLI** が送った model/path/header/tool schema と、次 turn の call/result replay。native helper/TTL、予約 hook、外部 task Read→予約→Edit→renew→release→Mail、複合 Bash 全体の拒否、receipt と lifecycle の観測。 |
| `test_instruments.py` | §7 の測定器の負対照 | 各測定器が、誤モデル・Chat path・code_mode・tool result 欠落・異なる auth header・漏えい・権限拡大・部分的 Bash 副作用・cloud fallback・偽 pre-exec・replay drift を実際に検出する。**製品や native CLI の合格ではない。** |

## 実行 / Run serially

Repo-local `.venv` は CONTRIBUTING.md の手順で作る。HOME/TMPDIR/runtime を
一時 directory に置き、load average が15未満のとき、一つずつ実行する。
本番ポート、tmux、Keychain、実 credential/API、実 profile は使わない。

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q -n 0 tests/r3_local_profiles/test_instruments.py
PYTHONPATH=. .venv/bin/python -m pytest -q -n 0 --tb=short tests/r3_local_profiles
```

1本目は green の測定器試験。2本目は、測定器以外が **missing implementation
で red (exit 1)** になるのが現在の期待である。skip/xfail/importorskip は使わず、
collection は完了させる。既存試験の代用や、merge 済みの機能の CI 成功とは数えない。
既存の `test_child_tools.py` / `test_codex_models.py` /
`test_settings_permissions.py` も同じ条件で変更前後に直列実行する。

The full run is deliberately failing and belongs in a draft PR. Do not hide its
failures with a marker, skip, or production shim. Existing regression tests must
still pass. Fixture listeners use OS-assigned loopback ports and are always shut
down; the in-process network guard rejects external hosts and live ports.

## 未実装の接続部 / Missing bindings, explicitly not evidence

以下は設計から切り出した **private seam の提案**であり、既存 API の主張ではない。
R3 実装時、既存共通経路の薄い test binding として名前を合わせてよい。契約の
期待値を捨てたり、test 内に resolver/permission engine を実装して green にしない。

| Module | Expected private seam |
| --- | --- |
| `local_model_profiles` | `LocalModelError`, `load_profile(root, ref, expected_uid=...) -> dict`, `profile_digest(dict) -> sha256` |
| `runtime_launch_intent` | `resolve_launch_intent(caller, context, source=...)`, `save/load_launch_intent`, `materialize_launch_intent`, `intent_digest`, `make_launch_receipt`, explicit `revise_launch_intent`, `project_local_model` |
| `local_model_auth` | `LocalModelError`, `run_auth(plan, executor=...) -> bytes`, `validate_runner(...)`; executor は credential command のみを代替する |
| `tests/r3_local_profiles/native_acceptance.py` | **未実装の test adapter** `probe(scenario, workspace=..., provider=..., ...) -> observations`。実装時に real pinned CLI・既存 launch/receipt・native permissions/hooks と fake Mail を接続する。製品 API として追加しない |

native test adapter も現在は missing import で red。これは「native を確かめた」
という意味ではない。green への条件は実 CLI の argv/env/cwd、native session ID、
peer の wire transcript、実ファイル副作用と hook/予約/Mail の観測を結び付けること。
trace を返すだけの偽 CLI/偽権限判定で代替してはならない。header は事前に
Bearer/X-Api-Key と決めず、実際に送った形式と peer の受理条件を照合する。
Bearer 専用・X-Api-Key 専用のうち非互換な Claude の組合せは unsupported が正解。
refresh は fixture clock の短縮と本番 TTL=300000ms の照合を分ける。

Native probes are specifications awaiting their integration adapter. Nothing in
this patch launches an installed CLI. Before enabling that adapter, use only
explicitly selected pinned executables, isolated provider homes and transports,
and deny non-fixture egress **including child processes**. The Python socket
guard alone does not contain subprocess networking. Do not auto-discover the
operator's logged-in CLI or reuse its live Mail/tmux/runtime. Authenticated native
peers use an ephemeral fixture CA and verified HTTPS; the native adapter must
explicitly trust only that fixture CA without disabling TLS verification. Plain
HTTP auth checks in instrument self-tests do not pass through the product loader.

`LOCAL_MODEL_REQUEST_FAILED` / `LOCAL_MODEL_PROTOCOL_FAILED` は受入 fixture 上の
仮ラベルであり、R2-Tb の既存 outcome/receipt reason へ binding 時に対応付ける。
exec 済み helper 失敗を pre-exec とすることや unknown の自動 retry は許さない。
canary はメモリ内照合のみで、peer transcript には header 名/形式/一致の真偽だけを残す。
鍵を fixture ファイル・環境・argv に入れず、failure message に値を出さない。

## 今回の範囲外 / Not established

R2-Ta/Tb の製品実装・全 suite・実機での GLM/量子化モデルの互換性・速度・品質、
実 provider の全 CLI 版、native settings の実効合成、実 auth helper header/refresh
はまだ未確認。cloud の既存正例は既存試験で守り、local 契約が green になった後も
全 caller 移植表と実機受入を置き換えない。挙動を変えない tests-only patch なので、
README/公開 user guide・API generation・製品 CHANGELOG は更新しない。
PR 用の tests-only fragment は追加し、`000000` は PR 作成後に実番号へ置き換える。

Wire instruments follow [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling)
and [Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events),
plus [Anthropic Messages streaming](https://platform.claude.com/docs/en/build-with-claude/streaming).
Their self-tests verify construction and rejection, not a provider's conformance.
