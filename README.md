# AI Dev Orchestrator

ブラウザーで操作できるAI開発パイプラインです。Web画面はPython標準ライブラリのHTTPサーバーで提供するため、StreamlitやWeb用の追加フレームワークは不要です。AIプロバイダーはGeminiとGitHub Copilotに対応しています。

## GitHub Codespacesで実行

1. Codespacesのターミナルで既存のワークフロー依存パッケージをインストールします。

   ```bash
   pip install -r requirements.txt
   ```

2. 認証情報の暗号化鍵は初回起動時にサーバー内へ自動生成されます。鍵の保存先は認証情報と同じディレクトリの `credentials.key` です。永続ディスクでこの鍵と `credentials.enc` の両方を保持してください。複数サーバー間で鍵を共有する場合や鍵管理サービスを使う場合は、環境変数 `APP_CREDENTIALS_KEY` で上書きできます。

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. GitHub OAuth Appを作成し、Device Flowを有効にして、AppのClient IDを `GITHUB_OAUTH_CLIENT_ID` 環境変数に設定します。GitHub認証はリポジトリ一覧取得と書き込みのため `repo` スコープを要求します。Codespaces以外の一般サーバーでも同じ設定が必要です。CopilotのDevice FlowはLiteLLMのGitHub Copilot連携を利用します。
4. アプリを起動します。

   ```bash
   python app.py
   ```

5. Codespacesの「ポート」タブでポート `8000` を開き、表示された転送URLにブラウザーでアクセスします。一般サーバーではリバースプロキシ等から公開URLへ転送してください。サーバーは `0.0.0.0:8000` で待ち受けます。

認証設定画面からGitHubとCopilotはブラウザーのDevice Flowでログインし、GeminiはAPIキーを登録します。暗号化された認証情報は既定で `~/.config/ai-dev-orchestrator/credentials.enc`、暗号化鍵は同じ場所の `credentials.key` に保存します。永続ディスクを使う一般サーバーでは、必要に応じて `APP_CREDENTIALS_FILE` で保存先を指定してください。GitHub/Copilotの有効期限が切れた場合や認証が拒否された場合は再ログインを促し、Gemini APIキーは失効が検出されるまで保存します。選んだリポジトリのデフォルトブランチへ同期します。`PORT` 環境変数を設定すると待ち受けポートを変更できます。

**運用上の注意:** このアプリ自体には利用者アカウント／セッション認証を実装していません。一般サーバーではHTTPSに加えて、認証済みリバースプロキシ、VPN、または同等のアクセス制限の内側に配置し、信頼できないネットワークへ直接公開しないでください。暗号化鍵と認証情報ファイルの両方を適切にバックアップし、鍵を紛失した場合は保存済み認証情報を復号できません。
