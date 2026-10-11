### 隔離 global server の予約・contact welcome を準備

S2c の schema5 に7つの公開 tool と既存 handshake の welcome を追加しました。予約は SQLite の未解放・期限前の行だけを ACTIVE とし、期限切れ履歴を書き換えません。旧 candidate の別 engine と activity/collector を削除し、共通 normalizer、S2b の transaction/receipt、通知の経路を共有します。更新・解放の省略選択、退役解放、管理 inspect/force/purge、歓迎 message の承認と中断後の再送を固定しました。

path による更新・解放を自己 ACTIVE だけに限定し、他 owner と自己の履歴が選択へ混ざらないようにしました。path・省略による解放は該当0件でも成功し、繰り返し実行できます。明示 ID の不存在・他 owner は全件拒否を維持します。session macro の inbox は共通の metadata 読取を使い、既知の coverage witness と dangling-anchor の回帰を保持します。

完成した receipt を持つ新規 candidate だけを起動し、旧 root は不変で拒否します。時刻の instant・ID・旧長 reason を保存する fresh proof と各保存境界の回帰を追加しました。既定旧 mode・既存 profile・activation=false は維持し、client/hook の結線と切替は後続です。[S2c の契約と移設](../docs/global-reservations.md)を参照してください。
