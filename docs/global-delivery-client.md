# 隔離 global client の起動と配送

[English](global-delivery-client.en.md)

既定は旧 mode のまま、global の `activation_enabled` は false です。この手順は専用の一時 HOME と、一時 server の完成済み candidate を使います。実サービスへの切替と released 形式の import は後続です。
Claude / Codex の固定 provider HOME は、起動前に `agentstack-runtime-client instruction-configure operator claude` / `codex` で指示 block を準備します。通常 launch は読むだけで、不足・古い版は `INSTRUCTION_PROVIDER_CONFIG_REQUIRED`。詳細は [指示の移行](instruction-migration.md#custom-provider-home) を参照してください。


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
| ended / cancelled | confirmed-ended の live 反映が全て済んでから次の明示起動を準備可能 |
| abandoned | 不明な process はそのまま。証明が得られたら明示解消 |

operator 操作は `agentstack-runtime-client operator-json OPERATION` に JSON を stdin から渡します。正本は [local_operator_wire.operations](../packages/agentstack_mail/fixtures/global-client-4c.json) です。`launch-cancel`・`launch-resolve`・`resolve-delivery` は `confirm:true` が必須、`launch-retry` は confirm を受け取りません。

`client_root` は対象の固定 client root、`AGENTSTACK_CLIENT_CONFIG` はその runtime-client.json を指します。launch ID は既存 launch.json、message ID は doctor-local の warning または受信した通知から取得します。次は launch の現在値を読む例です。
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

status の `phase`・`operator_required`・`next_operations`・`remote_owner_verified` を確認します。`state_sha256` は同じ lock 内の現在 context・credential・launch・live session と intent の digest です。状態や世代が変わって `RESOLUTION_DIGEST_MISMATCH` になったら、status を読み直して判断し直します。


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

操作後は再び status を読みます。abandon は `phase=abandoned / runtime_ready=false`、cancel は `phase=cancelled / prepared=true / runtime_ready=false`、retry は `phase=gated / prepared=true` を返し、trusted SessionStart が確認されるまでは ready になりません。delivery-status の digest を同じように取り、resolve-delivery へ `message_id`・`expected_state_sha256`・`confirm:true`・`resolution` を渡します。action は `retry-at-least-once` / `confirm-external-completed` / `abandon-without-consuming` で、operator_evidence に判断の根拠を記します。abandon-without-consuming は blocked にするだけで、未読や server の通知 fact を消費しません。

confirmed-ended の途中で live の保存が失敗した場合、status は `operator_required=true / next_operations=[launch-resolve]` を返します。新 digest で同じ confirmed-ended を再提示して残りを完了するまで次の launch は拒否されます。Mail に届かない場合も local status と abandon は使えます。
proxy は server fixture の schema から owner・binding の入力を除いて生成します。bootstrap と runtime_status は local tool です。bootstrap は trusted session receipt を確認します。名前や呼出し側が渡した owner token で宛先を選びません。新しい window は agent_id の既存 FK に束縛し、legacy_project_id は NULL にします。

## Codex hook trust

Claude の固定 provider HOME の mcp.json と orrery-hooks.json は導出物で、launch gate の operation mutex 内で再生成します。hooks は `--settings orrery-hooks.json` で渡し、provider が使う settings.json の model・permissions は変更しません。Gemini は native `.gemini/config/mcp_config.json` を唯一の設定として使い、同 mutex 下で `orrery-mail` entry だけを CAS 更新し、他の top-level key と MCP entry を保持します。Codex は既定 config を生成せず `-c` で呼出し時に指定します。同時書換えは `GENERATED_CONFIG_CHANGED`、読取不能は `GENERATED_CONFIG_UNREADABLE`、Gemini の JSON/構造不正は `GENERATED_CONFIG_JSON_INVALID` で上書きせず停止します。private path/permission または JSON を直して明示起動をやり直してください。設定の別の台帳はありません。

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
| tmux / standalone hook / persistent / daemon | 旧 watcher の同じ定義から読む30秒 cooldown、回数上限なし。unknown は timeout＋30秒の lease と backoff の両方を待つ |
| Codex App | poll 0.25秒、coalesce 2秒、1時間12 wake、lease 900秒、backoff 2..300秒、最大5 attempt（既定値。`AGENTSTACK_CODEX_APP_WAKE_*` を旧と同じ reader で適用） |

bare shell・capture timeout・session 不在・Mail disabled・未確定 route は handoff 前なので attempt を増やしません。App の rate 超過と5回上限は blocked とし、dead_letter にはしません。global watcher は server の signal directory がまだ無ければ作らず2秒 poll で待ち、存在後は directory の event で動き、event reader が無ければ旧と同じ2秒 poll、event の取りこぼしは既存 scan 間隔で救済します。待機期限・blocked は通信前に判定します。importance 未満は同 intent の BELOW_MIN_IMPORTANCE とし、下限設定が変わった時だけ再評価します。BELOW_MIN_IMPORTANCE と SELF_MESSAGE は operator 対応待ちではなく、doctor の警告には数えません。read/ack 等で hint が消えたら、既存の reconcile が同 intent を片付けます。

数値互換の対象は、先頭0のない10進と試験した5境界（0・1・300・301・非数字）です。先頭0の timeout は旧 watcher の算術が8進、global は10進で、旧 watcher 本体は変更していません。

unknown の再試行は duplicate_possible を返し、後の未送信だけでは先行 unknown の警告を消しません。同 owner/binding の再 launch は古い intent を新 attempt へ捕まえ直します。本当に owner/credential が変わった場合はその message だけ blocked とし、明示 retry で現在値へ再計画できます。完了後の空 slot は既存 message lock 内で rmdir し、以前に残った空 slot も次の走査で片付けます。client operation mutex は削除後まで保持するため、並行 prepare は新しい slot を開き、intent を失いません。intent の列挙では数字の候補名だけを取り、存在・実体・permission の確認は message lock 内へ集約します。不正な slot は diagnostics に出し、1 message / App root の異常は他の通知を止めません。confirmed 後は server completion の再送だけです。遅れて来た別 attempt・旧 credential generation・旧 launch の callback は拒否します。

server の top-level status は要求を受けられる readiness、signal.status は通知の配送の健康です。local unknown/blocked は既存 doctor の warning と status/worker log から確認します。dashboard が配送健康を読む UI は後続です。

不正 slot の `repair-local-state` は自動修復や CLI 名ではなく、表示した message の固定 consumer slot の permission/type を利用者が直す案内です。正常な slot は変更せず、symlink の先は辿らず、修復後に diagnostics を再実行してください。

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

<a id="operator-ui-access"></a>
## 隔離 dashboard の operator API

[English](global-delivery-client.en.md#operator-ui-access)

既定の導入・旧 dashboard は変わりません。API 世代15の global backend は、完成した S2c candidate を私有管理 socket 経由で読みます。dashboard は Mail の SQLite を開きません。公開 Mail tool の追加はありません。API 16 では `global_dashboard_ui=1`・`instruction_migration=1` として [global dashboard](dashboard.md#global-mail-view) を有効にします。cockpit の接続は後続です。API の世代だけで画面の対応を推測しないでください。

専用の一時 HOME を使い、`AGENTSTACK_RUNTIME_MODE=global`、`AGENTSTACK_GLOBAL_SERVER_CONFIG`（完成した candidate の `server-config.json`）、`AGENTSTACK_MCP_URL`（同じ隔離 Mail の HTTP endpoint）、`AGENTSTACK_HOME`（同じ isolation root の `agentstack`）、`AGENTSTACK_PORT`（専用の空き port）を明示します。設定が壊れたときに旧 DB へ戻ることはありません。実サービスの切替は別の手順です。

最初にローカルの operator が同じ隔離先の `AGENTSTACK_HOME` を明示して `agentstack-http-operator configure` を実行します。CLI も未設定なら `HTTP_OPERATOR_HOME_REQUIRED` で拒否し、実 HOME を既定にしません。固定の `runtime/http-operator.json` を0600で新規作成し、既存 file は上書きしません。`agentstack-http-operator show` で表示した値を HTTP の `Authorization: Bearer …` に渡します。これは agent の登録 token とは別です。値を URL、ログ、共有ファイルに載せないでください。失効させる場合は、利用する dashboard と cockpit を停止し、この固定 file を operator が退避してから configure し直します。

隔離 dashboard の URL を開き、Unlock にこの operator capability を貼り付けます。値は sessionStorage に保持して reload に使い、URL には入れません。同じ origin で開いている tab は BroadcastChannel で同じ capability を共有し、Lock も伝えます。別 origin は別に Unlock が必要です。Lock は参加中の online page の Mail と資格情報を消しますが、閉じた・復元した tab、opener・duplicate が持つ sessionStorage のコピー、ページへの権限を与えた拡張機能、別 browser profile のコピーは失効しません。すべてのコピーを失効させるには server の capability を再作成して再起動します。OS 利用者と browser profile が信頼境界です。誤った・古い capability は403、不在・安全でない capability file は503で、旧経路へ fallback しません。

HTTP は TCP 上の UID を直接証明できません。私有 file を読める利用者の capability を証明にし、loopback・設定済み Host・提示された Origin/Referer/Fetch Metadata を同じ入口で確認します。static の GET/HEAD 以外は、将来追加される route もこの入口を通ります。capability を盗める同 UID/root、XSS、ページへの権限を与えた拡張機能は、この境界では防げません。別の利用者と1台の Mail を共有する用途は支えません。

| 読取 | 選択 |
|---|---|
| `/api/agents`、`/api/mail-watcher-health` | operator の roster / 健康。本文なし |
| `/api/mail/recent`、`/api/mail/message`、`/api/mail/conversation` | 必須の `view_as_agent_id` の可視集合 |
| `/api/mail/legacy-label` | 同じ可視集合の旧 label。`source` は拒否 |
| `/api/agent-history` | 独立した target `agent_id`。可視集合との共通部分だけ |
| `/api/messages-since` | `after_id` より後の可視行を keyset で継続 |
| `/api/edge-messages` | `a_agent_id` と `b_agent_id` の可視な送受信だけ |

MAX・keyset・limit・返却 revision は1つの read transaction に置き、fetch で read/ack/通知を消費しません。`/api/graph` も同じ可視 metadata page を使います。見られない message、隠れた BCC、不可視な上限 ID を足して集計しません。

### 登録の状態と中断からの復旧

`agentstack-ui-client --client-name` は既存 CLI と同じ slug（小文字英数字で始まり、以降は小文字英数字・`_`・`-`、64文字以内）です。名前から別の root を自動生成しません。`clients --name NAME` で既存 root を探し、同名があればその root を選んでください。複数なら利用者が選びます。

| `status --client-name SLUG` | 次の明示操作 |
|---|---|
| absent | `prepare-root --client-name SLUG` |
| root_only / registration_pending | 同じ `register --client-name SLUG --name NAME --program PROGRAM --model MODEL` |
| registration_saved | `finalize-registration --client-name SLUG` |
| registered_unconfigured | 固定 context を指定し、既存の provider configure / Codex plan・人の承認・apply |
| configured | 既存の明示 launch |
| launch_present | 同じ launch ID と digest で既存の launch-status / resolution |
| unavailable | 原因を直す。別の identity は新しい root |

登録は既存の pending 1つに name/program/model/token/binding を保存し、同じ intent だけを再送します。未送信なら1行を作り、応答喪失なら原子的 NAME_CONFLICT→名前の解決→保存済み token の whois で同じ行を確認します。credential/context/metadata の保存途中でも、保存済み row の finalize は ID と token を保ち、再登録しません。status と一覧は provider の設定や context を書き換えません。

別 owner の同名だった場合、pending の SHA-256 を固定して `registration-abandon --client-name SLUG --expected-digest DIGEST` を明示できます。これは所有できない行を確認した場合だけ terminal にし、別の名前の登録や自動採用はしません。未送信・結果不明は同 intent の再送、所有できた行は finalize を使います。通常入口から terminal を解除せず、新 identity には新 root を使います。

`POST /api/global/spawn` はこの共通処理を使い、cwd を既存の spawn roots で確認します。provider の指示配置だけを明示 configure し、Codex trust は自動承認しません。必要な設定が無ければ registered_unconfigured を返します。`POST /api/global/resume` は同じ root の既存 launch/session を使い、operator resolution 待ちなら新しい launch を作りません。通信成功後に disk が失敗した場合も、status から同じ pending/finalize の入口へ戻ってください。


名前の照合で Mail に既存の名前があり、一致する client root が無ければ、root/pending の作成前に `NAME_CONFLICT` を返します。`clients` の `name_agent_id` はその読取結果、`diagnostics` は root ごとの異常です。壊れた root は利用できませんが、他の root の一覧と別名の新規登録は続けられます。terminal pending は名前と owner conflict の証拠として保持し、同名の再利用を拒否します。退役済み identity の照合も管理側の owner 検証で行います。

登録直前の競合で pending が残った場合は、`GET /api/global/spawn-status?client_name=SLUG` の `registration_sha256` を確認し、同じ HTTP admission の下で `POST /api/global/registration-abandon` に `{"client_name":"SLUG","expected_digest":"DIGEST"}` を渡せます。digest 不一致では記録を変えません。既存 CLI と同じく別 owner と確認できた intent だけを terminal にし、別の identity には新 root を使います。root または登録 pending が無い場合は404 `TARGET_UNAVAILABLE` です。不正な既存 file の拒否と digest の照合は維持します。token は status に返しません。不正 JSON は400 `QUERY_INVALID`、登録の競合は409、transport などの障害は503です。
