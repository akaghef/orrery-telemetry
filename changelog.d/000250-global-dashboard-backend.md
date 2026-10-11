### 隔離 dashboard の読取と登録の共通入口

API 世代15に、私有 operator capability の HTTP 入場条件と管理 socket の ui_read を追加しました。公開 Mail tool は増やさず、可視集合・MAX・keyset・revision を同じ読取 transaction で確認し、dashboard は global の DB を開きません。登録と子登録は同じ pending・同じ token の再送と finalize を共用し、固定 root の状態・同名の既存 root を読取だけで確認できます。既定は旧 mode・activation=false のままで、画面と cockpit の接続は後続です。

