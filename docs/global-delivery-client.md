# 隔離 global client の起動と配送

[English](global-delivery-client.en.md)

既定は旧 mode のまま、global の `activation_enabled` は false です。この手順は専用の一時 HOME と、一時 server の完成済み candidate を使います。実サービスへの切替と released 形式の import は後続です。

## 正本と固定配置

owner と binding は既存の context・credential、window は context.identity だけに保存します。旧 global credential/profile/session の window 欄は読取で書き換えず無視し、次の正規書込みで省きます。現在の PID・pane・provider session は既存 live-sessions と session_index に保存します。`runtime/launch.json` は起動 ID、nonce、phase、provider、種別、cwd、task digest、異常と明示解消だけを持ちます。

通知の完了は server の notification fact が正本です。consumer の intent は外部操作の中断と再試行に使い、成功の cache にはしません。取得は純粋な読取で、明示 read・ack・配送完了だけが通知を消費します。

consumer は `agentstack_home/global-consumer/<sha256(mail_instance_id)>/agents/<agent_id>` です。既存 server-config.json の isolation・instance・candidate generation・epoch・runtime_root・authority・lock が一致しなければ `CONSUMER_ISOLATION_MISMATCH` で出力前に止めます。wire v3 の authority からの導出を訂正し、server preparation workspace に client の状態を置きません。同 instance の各 client は同じ consumer に達します。

agent の route.json と App の external route は client_name だけを保存します。token・表示名・現在 launch の写しはありません。client ごとに owner の固定 profile・session・credential を読み直します。

## 起動と中断からの戻り方

`agent-start` / `agent-start-codex` / `agent-start-gemini` と子の spawn/resume は共通 runtime helper を使います。cwd・command・出力 slot・capability を先に確認し、window の bind と一意な launch の gate を経て起動します。登録 pending と launch は別の操作です。起動を再登録で回避しません。

| phase | 戻り方 |
| --- | --- |
| planned / gated | status の digest を指定して launch-retry または launch-cancel |
| exec-unknown | 自動で再実行せず launch-resolve で abandon / confirmed-running / confirmed-ended |
| running | trusted SessionStart receipt と owner/window の確認が揃ったときだけ runtime_ready |
| ended / cancelled | 次の明示起動を準備可能 |
| abandoned | 不明な process はそのまま。証明が得られたら明示解消 |

operator 操作は `agentstack-runtime-client operator-json OPERATION` に JSON を stdin から渡します。`launch-status` / `delivery-status` の `state_sha256` を取得し、変更操作には `expected_state_sha256` と `confirm: true` を渡します。digest は同じ lock 内の現在 context・credential・launch・live session と intent から計算します。status 後に世代や起動が変われば解消を拒否します。Mail に届かない場合も local status と abandon は使えます。

proxy は server fixture の schema から owner・binding の入力を除いて生成します。bootstrap と runtime_status は local tool です。bootstrap は trusted session receipt を確認します。名前や呼出し側が渡した owner token で宛先を選びません。新しい window は agent_id の既存 FK に束縛し、legacy_project_id は NULL にします。

## Codex hook trust

通常の launch は trust を承認しません。固定 provider HOME の承認済み plan、plugin payload、native config の trust が一致しなければ `CODEX_HOOK_CONFIGURE_REQUIRED` で止まります。変更前の plan に承認を使い回すこともできません。

operator は次の手順で現在の配置を準備します。`AGENTSTACK_CLIENT_CONFIG` は対象の固定 context を指します。

```sh
agentstack-runtime-client codex-plan operator > hook-plan.review.json
```

表示された provider HOME・plugin 配置・hook の内容を人が確認し、承認する場合だけ、その JSON の `approved` を `true` に編集します。承認済み JSON を stdin で渡します。

```sh
agentstack-runtime-client codex-apply operator < hook-plan.review.json
```

既存の plan / apply_trust 処理を使います。通常の起動、更新、再開が自動で承認する入口はありません。別 HOME・任意の配置・別版 binary の導入は行いません。plugin の Mail MCP は無効にし、起動時に渡す同一 client の proxy だけを使います。hook 自体の承認は保持します。

Gemini は固定 provider HOME の `.gemini/config/mcp_config.json` と既存の stream renderer を使います。headless の子は実行した provider の `init/conversation_id` だけを session receipt にします。init が無ければ `GEMINI_SESSION_RECEIPT_UNAVAILABLE`、対話型 root は `GEMINI_INTERACTIVE_RUNTIME_UNSUPPORTED` で止まり、ready になりません。Gemini は合成 stream provider による fixture 試験のみで、実 agy では未確認です。

## persistent の operator configure

固定の profile.json に実行設定 `execution` を1つだけ保存します。owner/binding は追加しません。通常 run は設定を読むだけです。旧 persistent profile は `LEGACY_FILE_REQUIRES_IMPORT` のまま、PR7 の明示 import で移します。

```sh
agentstack-persistent configure --profile "$client_root/profile.json" --operator <<EOF_JSON
{"argv":["/absolute/python","/absolute/bot.py"],"interaction":"headless","cwd":"/absolute/private-workdir"}
EOF_JSON
agentstack-persistent run --profile "$client_root/profile.json"
```

argv は配列だけを受け、shell の文字列は拒否します。cwd は同 isolation 内の private・no-symlink な directory です。実行設定が無い run は `PERSISTENT_EXECUTION_REQUIRED` です。任意の profile 保存先や旧 profile の並行読取はありません。

bot は受け取った `AGENTSTACK_CLIENT_CONFIG` で共通 client を使います。trusted SessionStart を記録した後、固定 `AGENTSTACK_PERSISTENT_WAKE_SOCKET` で既存 `orrery-mail-notification-reply-v1` を受けます。通知には message と `reply_request_id` があり、同じ通知の再試行でもこの UUID は一定です。reply_message の intent を同 UUID で完了し、それから元 message_id を付けた `status: replied` を返します。reply 前の終了や不明応答では配送完了にしません。

## 通知の再試行と健康

hint は ID と binding だけです。owner の pending-only・exact-ID fetch で確かめた message だけを外部へ渡します。purged/read/ack/imported-consumed の hint は再注入しません。全文・preview・本文無しの文言は1つの formatter から生成します。

| channel | 再試行 |
| --- | --- |
| tmux / standalone hook / persistent / daemon | 旧 watcher の30秒 cooldown、回数上限なし。unknown は timeout＋30秒の lease と backoff の両方を待つ |
| Codex App | poll 0.25秒、coalesce 2秒、1時間12 wake、lease 900秒、backoff 2..300秒、最大5 attempt |

bare shell・capture timeout・session 不在・Mail disabled・未確定 route は handoff 前なので attempt を増やしません。App の rate 超過と5回上限は blocked とし、dead_letter にはしません。unknown の再試行は duplicate_possible を返します。confirmed 後は server completion の再送だけです。遅れて来た別 attempt・旧 credential generation・旧 launch の callback は拒否します。

server の top-level status は要求を受けられる readiness、signal.status は通知の配送の健康です。local unknown/blocked は既存 doctor の warning と status/worker log から確認します。dashboard が配送健康を読む UI は後続です。

## lock と隔離試験

取得順は agent directory A → client operation C → message directory M → mutation U → authority F です。必要なものだけ取得し、外部 provider・tmux・bot の応答待ち・hook stdout flush の前に全て解放します。新しい launch.lock や配送 DB を作りません。SQLite の main/WAL/SHM/journal は client が開閉・copy・hash しません。

試験は実 HTTP health まで待つ一時 server、一時 HOME、専用 tmux socket を使います。installer の client payload は既存の宣言1か所から配布し、試験用 layout もその宣言から取得します。全体と sandbox 外の試験は作者の関連試験と分けて記録します。

管理 callback の delivery_completed だけは、通知後に宛先が退役しても既存 recipient と厳密な credential generation を照合して完了を記録します。公開 tool の退役拒否は維持し、再有効化後も同じ通知を再配送しません。

子の task_delivery=mail は、親の既存 mutation intent と task digest 由来の同じ UUID で send_message を再送します。本文を起動引数に埋めず、子は inbox から取得します。連絡承認は通常の contact policy に従い、自動では承認しません。tools_selection は既存 child_tools の方針を固定 provider HOME の設定に適用し、選択した server が無ければ TOOL_SELECTION_UNAVAILABLE で止めます。

### 明示的な Codex plan のやり直し

`codex-plan` が marketplace の作成後に中断した場合は、利用者が同じコマンドを明示的に再実行します。再構築するのは固定 provider HOME の `marketplace/` だけです。再実行は常に未承認の plan を返します。同じ内容でも再承認を必要とする一つの規則に揃え、古い承認が別の内容に使われることを防ぎます。通常の launch は再構築も承認も行いません。

### App の cold wake の範囲

App の履歴 HOME は旧 consumer と同じ共通関数で決めます。`CODEX_HOME` が無ければ通常の `~/.codex` です。履歴を子 root に複製・移動せず、resume の process 以外は書き換えません。resume には対象の子 context の proxy を渡し、単一 root の同時 resume を避けます。既存の plugin と人が承認した native trust を利用し、自動承認しません。通常の子 launch は引き続き子の固定 provider HOME を使います。

App の cold wake は fixture のみ、実 resume は未確認です。1時間12回に達すると `blocked / WAKE_RATE_LIMITED` になり、handoff の試行回数を増やしません。時間枠が空けば同じ intent から再開します。通知 fact は server に残り、poll は消費しません。

App の `AGENTSTACK_CODEX_APP_COLD_WAKE`、`AGENTSTACK_CODEX_BINARY`、`AGENTSTACK_CODEX_APP_PLUGIN_ID`、`AGENTSTACK_CODEX_APP_SKIP_GIT_CHECK` は旧 consumer と同じ設定 reader を使います。cold wake の明示停止と Mail disabled の間は新しい handoff を作りません。
