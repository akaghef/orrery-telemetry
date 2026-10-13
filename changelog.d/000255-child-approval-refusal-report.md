### 子が MCP tool の承認拒否を親に報告して停止

Codex・Claude に配布する managed 指示に、`approval policy is never` などで MCP tool が拒否された場合の報告手順を追加しました。子は利用者の設定を書き換えず、拒否された tool 名と `agentstack-resume <自分の名前> --tools <spec>` が必要な旨を ORRERY Mail で親へ1行報告して停止します。親が無い場合や Mail を使えない場合は利用者に伝えて停止します。[子に渡す道具](../docs/delegation.md)に、新規起動と再開の修復経路・制限を記載しました。
