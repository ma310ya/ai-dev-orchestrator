# AI Dev Orchestrator

ブラウザーで操作できるAI開発パイプラインです。Web画面はPython標準ライブラリのHTTPサーバーで提供するため、StreamlitやWeb用の追加フレームワークは不要です。

## GitHub Codespacesで実行

1. Codespacesのターミナルで既存のワークフロー依存パッケージをインストールします。

   ```bash
   pip install requests litellm langgraph
   ```

2. Codespacesのシークレットに、利用するモデルプロバイダーのAPIキー（例: `GEMINI_API_KEY`）を設定します。GitHubリポジトリ一覧と書き込みにはCodespacesで認証済みの `gh` CLIを使用します。`gh auth token` が利用できない環境では、リポジトリ読み取りとContents書き込み権限のある `GITHUB_TOKEN` または `GH_TOKEN` を設定してください。
3. アプリを起動します。

   ```bash
   python app.py
   ```

4. Codespacesの「ポート」タブでポート `8000` を開き、表示された転送URLにブラウザーでアクセスします。サーバーは `0.0.0.0:8000` で待ち受けます。

対象リポジトリはログイン中のGitHubアカウントが書き込み可能なものから動的に取得します。選択時にも権限を再確認し、選んだリポジトリのデフォルトブランチへ同期します。`PORT` 環境変数を設定すると待ち受けポートを変更できます。実行中のログは画面に表示され、処理のキャンセルもブラウザーから要求できます。
