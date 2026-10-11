### 登録 pending が無い場合の明示解消

利用者向けの登録解消で root または pending が無いときは、503 ではなく404 `TARGET_UNAVAILABLE` を返します。HTTP の認可、既存 file の検証、digest の一致確認は維持します。
