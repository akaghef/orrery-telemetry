# tmux consumer の共有境界

この draft の共有 helper は、旧 consumer の transport 固定と対象保護のためのものです。公開 Mail tool・dashboard API は増えません。既定の legacy と activation=false は維持し、専用 server での新規 global 起動はまだ有効にしません。cockpit の global terminal/protocol 移植は後続です。

## CLI と schema

入口は既存の `agentstack-runtime-client` です。JSON の正本は `bin/lib/runtime_tmux_contract.json`、検証は既存の `schema_contract.py` を共用します。

| command | 入力 | 出力 / 用途 |
|---|---|---|
| `tmux-capabilities` | なし | `contract=orrery-tmux-target-v1` と `legacy-transport` / `legacy-target-admission`。版文字列だけで対応を判断しない |
| `tmux-legacy-transport` | なし | 照合済み socket・tmux の argv prefix・server PID・消す環境変数。consumer の起動時に固定する |
| `tmux-legacy-target` | `--session NAME` または `--pane %N`、`--operation capture\|attach\|input\|split\|close\|kill-session` | 許可した対象の pane/PID/start と digest。拒否は exit2 と固定 reason。consumer は拒否対象に副作用を起こさない |

server がまだ無い既定 legacy 起動は、接続先だけを返し server_pid は null です。socket/server/mutex を作らず、対象への操作は server の実体が確認できるまで拒否します。

全 command は読取専用です。任意 socket/root の CLI 引数、token、owner 認証の代用は受けません。返す `environment_unset` は `TMUX` と `TMUX_PANE` を消す差分です。親の全環境や credential を JSON に出しません。global owner の target/attach/compact/end は別の権限確認が必要で、この capability では提供しません。

consumer は起動時に返った socket/server PID を固定し、各操作でその同じ transport の admission を直前に取り直します。返った argv prefix に操作を追加し、`environment_unset` を適用します。名前だけの bare tmux に戻らず、明示 `-S` を維持します。session を消す操作は対象 session の全 pane を照合します。pane番号単独や名前 prefix は所属の証明にしません。

## 既定 endpoint と対象の保護

既定 endpoint は `TMUX_TMPDIR` と UID から決めます。継承した custom `TMUX` は UID所有/private directory の socket と実 server PID の一致を確認します。新しい global 固定 directory を legacy endpoint として使う起動は `GLOBAL_TMUX_LEGACY_CONSUMER_REFUSED` で拒否します。socket は tmux 標準の0660も許容しますが、親 directory は同 UID の0700を必須にして他者の到達を遮断します。既存 socket の chmod は行いません。

global 専用 endpoint の導出関数は、canonical client home と Mail instance の hash から短い絶対 path を作ります。epoch/TMPDIR/深いHOMEで endpoint を変えず、socket registry は作りません。この段階では新規起動から呼び出しません。

旧対象の保護は、固定 `clients/` の context/credential/launch/live を既存の read-only snapshot 入口で照合します。新しい mutex・launch・親子台帳・SQLite handle は作りません。現在の pane/PID/start に global の既存 live が対応すれば `LEGACY_TARGET_IS_GLOBAL`。root が壊れている/partial/照合不能なら、その target の操作を拒否します。helper が古い/無い場合の consumer は固定 layout の負の admission を使い、global 不在を証明できた純旧配置だけ従来の操作を続けます。

拒否対象の通知を成功消費せず、既存の理由表示/再試行に残します。別の正常 legacy target の処理は続けます。read-only な一覧を一括停止するための境界ではありません。repair は固定 client root/config を確認して同じ操作を再試行します。他人の receipt を借りたり、未知の対象を名前で採用したりしません。

socket・pane一覧・PID/startは照合の前後で再確認します。ただし admission と外部 tmux 副作用の間を OS の原子的 transaction にするものではありません。同UIDの非協調 process による差し替えを完全に封じる権限境界とは説明しません。consumer は待機を挟まず直前に確認し、対象が変われば拒否します。

## 実装段階

Telemetry の transport/consumer と公開 cockpit の旧対象保護を別 PR にします。共有 CLI をこの draft で固定し、cockpit の fixture にはこの実コードと installer 正本の payload を配備します。双方の merge と配布 launcher の対応前に専用起動を有効にしません。新しい rollout flag や確認台帳を作りません。

試験は一時 HOME、短い一時 directory、隔離した `-L` server を使い、実行境界は照合済みの絶対 `-S` へ固定します。既定の live tmux server、実 provider HOME、実 Mail DB は使いません。
