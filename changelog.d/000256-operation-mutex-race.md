### client の mutex file を作成途中に読むと壊れて見える race を修正

client root の operation mutex と、送信の local mutex は、file を `O_EXCL` で作ってから JSON を書いていました。その間に別の caller が同じ file を読むと、空の file を読んで `PRIVATE_JSON_INVALID` で失敗していました（同じ client root を並行して prepare したときなど）。作成中に process が止まると、空の file が残って以後ずっと失敗することもありました。

完全な内容を private な一時 file に書いてから rename で置くように変えました。読む側には、file が無いか、内容が揃った file のどちらかしか見えません。作成の排他は親 directory の lock で取り、作成の間だけ持ちます。すでに置かれた mutex の inode は置き換えないので、別の caller が持っている lock を上書きしません。
