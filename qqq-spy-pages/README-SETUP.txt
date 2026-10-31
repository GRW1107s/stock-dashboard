QQQ / SPY 日次騰落率ダッシュボード（GitHub Pages用）

1) このZIPをWindowsで「すべて展開」してください。
2) https://github.com/GRW1107s/stock-dashboard を開いてください。
3) Add file → Upload files から、展開したフォルダの以下をアップロードしてください。
   index.html
   scripts フォルダ
   .github フォルダ
   ※ ZIPそのものをアップロードしても動きません。
   ※ .github/workflows/pages.yml という階層を維持してください。
   ※ 元の index.html は新しい同名ファイルで上書きします。
4) Commit changes で保存してください。
5) Settings → Pages → Build and deployment → Source を「GitHub Actions」に変更してください。
6) Actions → Update QQQ-SPY and publish Pages → Run workflow → Run workflow で手動実行します。
7) 緑のチェックが付いたら https://grw1107s.github.io/stock-dashboard/ を開きます。

表示内容
- date（yyyy/mm/dd）、QQQ、SPYの3列
- 米国取引日、最新の営業日が最上段
- %は前営業日終値からの価格変動率（小数点第2位）、配当は含みません。
- 上昇=緑、下落=赤
- 直近30日 / 90日 / 全期間、CSVダウンロード
- GitHub Actionsで日本時間の火〜土 10:17、17:17 に更新予定（実行遅延やデータ提供側の遅れあり）

データ取得について
- Yahoo Financeの非公式チャートエンドポイントを利用します。
- 無料ですが提供元の仕様変更・アクセス制限・429等で失敗することがあります。
- 取得失敗時にはGitHub Actionsが赤い×になり、直前に成功した公開サイトは残ります。
- 取得処理にAPIキーや外部Pythonライブラリは不要です。
- まだ株価の数字は同梱していません。初回Actions実行後に実データが入ります。
