### mode の選択を client・dashboard・service で共通化

- mode の選択を owner の認証から分離し、context のない operator/service が global 選択後に旧 Mail へ戻らないようにしました。既定の legacy と activation=false は維持します。
- 新規 process は既存の設定から mode を一度選び、稼働中の hook・worker は継承します。明示 mode と context/config の食い違いは理由付きで拒否します。
- context のない watcher は既存の route ごとに共通配送処理を使い、不正な宛先を分離します。詳しくは [global delivery client](../docs/global-delivery-client.md) を参照してください。
