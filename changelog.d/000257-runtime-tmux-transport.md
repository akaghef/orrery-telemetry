### tmux の接続先と旧 consumer の対象保護を共有

- 固定 client root の既存 launch/live を読取だけで照合する共有 helper を追加しました。旧 consumer が global pane に作用する操作や、所属を確認できない対象への操作を理由つきで拒否できます。
- 既定 legacy・activation=false を維持し、専用 global 起動はこの段階では有効にしません。公開 Mail tool と dashboard API の変更はありません。
- [共有境界の契約](../docs/runtime-tmux.md) と [English contract](../docs/runtime-tmux.en.md) を追加しました。
