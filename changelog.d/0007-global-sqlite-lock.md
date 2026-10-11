### global SQLite の POSIX lock を保つ

S1 からの不具合を修正しました。main DB と sidecar の安全確認、および source の形式確認で独立した fd を開閉せず、`lstat` を使います。同じ process の SQLite lock が解除され、並行処理で DB が壊れる経路を取り除きました。別 process が保持中の writer lock を取得できない回帰と、DB/sidecar の raw open を禁止する回帰を追加しました。監査結果は日英の global server ガイドに記録しています。
