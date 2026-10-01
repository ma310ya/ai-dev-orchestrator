import ast
import html
import json
import os
import re
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from main import (
    dev_workflow,
    get_writable_github_repositories,
    get_writable_github_repository,
    set_runtime_callbacks,
)


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
MAX_REQUEST_SIZE = 1024 * 1024
MODEL_SUGGESTIONS = [
    "gemini/gemini-3.8-flash",
    "gpt-4o",
    "gpt-4o-mini",
    "o3-mini",
    "anthropic/claude-3-5-sonnet-20241022",
    "groq/llama-3.3-70b-versatile",
]
STDLIB_MODULES = set(__import__("sys").builtin_module_names) | {
    "os", "sys", "time", "re", "math", "json", "glob", "shutil", "pathlib",
    "datetime", "subprocess", "random", "collections", "itertools", "functools",
    "typing", "typing_extensions", "unittest", "logging", "hashlib", "io",
    "tempfile", "traceback", "copy", "threading", "multiprocessing", "queue",
    "socket", "http", "urllib", "email", "csv", "sqlite3", "base64", "uuid",
}
PACKAGE_MAPPING = {
    "cv2": "opencv-python-headless",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
}

STATE_LOCK = threading.Lock()
CANCEL_EVENT = threading.Event()
STATE = {
    "running": False,
    "logs": [],
    "spec": "",
    "result": None,
    "error": "",
    "form": {},
    "last_request_text": "",
}


def extract_pip_packages(code):
    packages = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            package = name.split(".")[0]
            if package and package not in STDLIB_MODULES:
                packages.add(PACKAGE_MAPPING.get(package, package))
    return sorted(packages)


def parse_form(handler):
    content_type = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length", "0"))
    if length < 0 or length > MAX_REQUEST_SIZE:
        raise ValueError("送信サイズは1MB以下にしてください。")
    body = handler.rfile.read(length)
    if content_type.lower().startswith("multipart/form-data"):
        envelope = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
            + body
        )
        message = BytesParser(policy=default).parsebytes(envelope)
        fields = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            value = part.get_payload(decode=True) or b""
            if part.get_filename():
                if name == "spec_file" and value:
                    fields["uploaded_spec"] = value.decode("utf-8")
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = value.decode(charset)
        return fields

    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def validate_target_file(target_file):
    if (
        not target_file
        or target_file in {".", ".."}
        or "/" in target_file
        or "\\" in target_file
        or Path(target_file).name != target_file
        or not target_file.endswith(".py")
        or target_file in {"app.py", "main.py"}
    ):
        raise ValueError("対象ファイルには app.py / main.py 以外の .py ファイル名を指定してください。")
    return target_file


def start_workflow(form):
    target_file = validate_target_file(form.get("target_file", "").strip())
    repository = get_writable_github_repository(form.get("github_repo", "").strip())
    request_text = form.get("request", "").strip()
    if not request_text:
        request_text = form.get("uploaded_spec", "").strip()
    with STATE_LOCK:
        if STATE["running"]:
            raise ValueError("別のパイプラインが実行中です。")
        saved_spec = form.get("spec", "").strip()
        if request_text != STATE["last_request_text"]:
            saved_spec = ""
        STATE["last_request_text"] = request_text
        STATE["running"] = True
        STATE["logs"] = []
        STATE["spec"] = saved_spec
        STATE["result"] = None
        STATE["error"] = ""
        STATE["form"] = {
            "target_file": target_file,
            "github_repo": repository["full_name"],
            "github_branch": repository["default_branch"],
            "plan_model": form.get("plan_model", MODEL_SUGGESTIONS[0]).strip(),
            "code_model": form.get("code_model", MODEL_SUGGESTIONS[0]).strip(),
            "request": request_text,
        }
        worker_form = STATE["form"].copy()
        CANCEL_EVENT.clear()

    worker = threading.Thread(
        target=run_workflow,
        args=(worker_form, saved_spec),
        daemon=True,
    )
    worker.start()


def run_workflow(form, saved_spec):
    def log_status(message):
        with STATE_LOCK:
            STATE["logs"].append(str(message))

    set_runtime_callbacks(status_cb=log_status, cancel_fn=CANCEL_EVENT.is_set)
    try:
        existing_code = ""
        target_path = Path(form["target_file"])
        if target_path.is_file():
            existing_code = target_path.read_text(encoding="utf-8")
        result = dev_workflow.invoke(
            {
                "request": form["request"],
                "target_file": form["target_file"],
                "existing_code": existing_code,
                "plan_model": form["plan_model"],
                "code_model": form["code_model"],
                "github_repo": form["github_repo"],
                "github_branch": form["github_branch"],
                "spec": saved_spec,
                "code": "",
                "test_result": "",
                "iteration": 0,
                "is_success": False,
            },
            config={
                "configurable": {"thread_id": f"dev_{form['target_file']}"},
                "recursion_limit": 15,
            },
        )
        with STATE_LOCK:
            STATE["result"] = {
                "is_success": bool(result.get("is_success")),
                "spec": result.get("spec", ""),
                "code": result.get("code", ""),
                "test_result": result.get("test_result", ""),
                "packages": extract_pip_packages(result.get("code", "")),
            }
            if result.get("spec"):
                STATE["spec"] = result["spec"]
    except Exception as error:
        with STATE_LOCK:
            STATE["error"] = str(error)
    finally:
        set_runtime_callbacks(None, None)
        with STATE_LOCK:
            STATE["running"] = False


def render_page(error=""):
    with STATE_LOCK:
        form = STATE["form"].copy()
        spec = STATE["spec"]
        running = STATE["running"]
    target_files = sorted(
        path.name
        for path in Path(".").glob("*.py")
        if path.name not in {"app.py", "main.py"}
    )
    options = "".join(
        f'<option value="{html.escape(name, quote=True)}"></option>'
        for name in MODEL_SUGGESTIONS
    )
    target_options = "".join(
        f'<option value="{html.escape(name, quote=True)}"></option>'
        for name in target_files
    )
    error_html = (
        f'<p class="error">{html.escape(error)}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI開発自律オーケストレーター</title>
  <style>
    body {{ font: 16px system-ui, sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #1f2937; }}
    h1 {{ color: #065f46; }} label {{ display: block; font-weight: 600; margin-top: 1rem; }}
    input, textarea {{ box-sizing: border-box; width: 100%; padding: .65rem; margin-top: .35rem; border: 1px solid #9ca3af; border-radius: 5px; }}
    textarea {{ min-height: 130px; }} button {{ padding: .7rem 1.1rem; border: 0; border-radius: 5px; cursor: pointer; }}
    .run {{ background: #10b981; color: white; font-weight: 700; }} .cancel {{ background: #fff; color: #dc2626; border: 1px solid #dc2626; }}
    .actions {{ display: flex; gap: .75rem; margin-top: 1.25rem; }} .panel {{ margin-top: 1.5rem; padding: 1rem; background: #f3f4f6; border-radius: 6px; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }} .error {{ color: #b91c1c; }} .hint {{ color: #4b5563; font-size: .9rem; }}
  </style>
</head>
<body>
  <h1>🤖 AI開発自律オーケストレーター</h1>
  <p class="hint">この画面はPython標準ライブラリのHTTPサーバーで動作します。GitHubにログイン済みのアカウントから書き込み可能なリポジトリを取得します。</p>
  {error_html}
  <form method="post" action="/run" enctype="multipart/form-data">
    <label>対象GitHubリポジトリ
      <select id="github-repo" name="github_repo" data-selected="{html.escape(form.get("github_repo", ""), quote=True)}" required>
        <option value="">リポジトリ一覧を読み込み中...</option>
      </select>
    </label>
    <p id="repo-error" class="error"></p>
    <label>対象ファイル (.py)
      <input name="target_file" list="target-files" value="{html.escape(form.get("target_file", "new_tool.py"), quote=True)}" required>
      <datalist id="target-files">{target_options}</datalist>
    </label>
    <label>Planモデル名
      <input name="plan_model" list="models" value="{html.escape(form.get("plan_model", MODEL_SUGGESTIONS[0]), quote=True)}" required>
    </label>
    <label>Codeモデル名
      <input name="code_model" list="models" value="{html.escape(form.get("code_model", MODEL_SUGGESTIONS[0]), quote=True)}" required>
      <datalist id="models">{options}</datalist>
    </label>
    <label>要件・指示・エラーログ
      <textarea name="request" placeholder="作成・修正したい内容を入力してください">{html.escape(form.get("request", ""))}</textarea>
    </label>
    <label>要件定義ファイル (.md / .txt)
      <input type="file" name="spec_file" accept=".md,.txt,text/plain">
    </label>
    <label>保存済み仕様書（編集可能）
      <textarea id="spec" name="spec">{html.escape(spec)}</textarea>
    </label>
    <div class="actions">
      <button id="run" class="run" type="submit">🚀 開発パイプラインを実行</button>
      <button id="cancel" class="cancel" type="button" onclick="cancelRun()">🛑 処理をキャンセル</button>
    </div>
  </form>
  <section class="panel">
    <h2 id="status">待機中</h2>
    <pre id="logs">実行ログはここに表示されます。</pre>
  </section>
  <section class="panel" id="result" hidden>
    <h2>実行結果</h2>
    <p id="result-message"></p>
    <p id="packages"></p>
    <h3>仕様</h3><pre id="result-spec"></pre>
    <h3>コード</h3><pre id="result-code"></pre>
    <h3>エラー詳細</h3><pre id="result-error"></pre>
  </section>
  <script>
    async function loadRepositories() {{
      const select = document.getElementById('github-repo');
      const error = document.getElementById('repo-error');
      try {{
        const response = await fetch('/repos', {{ cache: 'no-store' }});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'リポジトリ一覧を取得できませんでした。');
        select.replaceChildren(new Option('リポジトリを選択してください', ''));
        for (const repo of data.repositories) {{
          select.add(new Option(repo.full_name + ' (' + repo.default_branch + ')', repo.full_name));
        }}
        select.value = select.dataset.selected;
        if (!data.repositories.length) error.textContent = '書き込み可能なリポジトリがありません。';
      }} catch (exception) {{
        select.replaceChildren(new Option('リポジトリ一覧を取得できません', ''));
        error.textContent = exception.message;
      }}
    }}
    async function updateStatus() {{
      try {{
        const response = await fetch('/status', {{ cache: 'no-store' }});
        const data = await response.json();
        document.getElementById('status').textContent = data.running ? '実行中' : (data.result ? (data.result.is_success ? '完了' : '終了') : '待機中');
        document.getElementById('logs').textContent = data.logs.join('\\n') || '実行ログはここに表示されます。';
        document.getElementById('run').disabled = data.running;
        document.getElementById('cancel').disabled = !data.running;
        const spec = document.getElementById('spec');
        if (document.activeElement !== spec && data.spec) spec.value = data.spec;
        if (data.result || data.error) {{
          const panel = document.getElementById('result');
          panel.hidden = false;
          document.getElementById('result-message').textContent = data.error || (data.result.is_success ? 'パイプラインが正常に完了しました。' : 'パイプラインが失敗しました。');
          document.getElementById('result-spec').textContent = data.result ? data.result.spec : '';
          document.getElementById('result-code').textContent = data.result ? data.result.code : '';
          document.getElementById('result-error').textContent = data.result ? data.result.test_result : '';
          document.getElementById('packages').textContent = data.result && data.result.packages.length ? '必要パッケージ: pip install ' + data.result.packages.join(' ') : '';
        }}
      }} catch (error) {{
        document.getElementById('status').textContent = '状態の取得に失敗しました: ' + error;
      }}
    }}
    async function cancelRun() {{
      await fetch('/cancel', {{ method: 'POST' }});
      updateStatus();
    }}
    loadRepositories();
    updateStatus();
    setInterval(updateStatus, 1500);
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/repos":
            try:
                payload = {"repositories": get_writable_github_repositories()}
                status = 200
            except RuntimeError as error:
                payload = {"repositories": [], "error": str(error)}
                status = 503
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/status":
            with STATE_LOCK:
                payload = {
                    "running": STATE["running"],
                    "logs": STATE["logs"][:],
                    "spec": STATE["spec"],
                    "result": STATE["result"],
                    "error": STATE["error"],
                }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/":
            self.send_html(render_page())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/cancel":
            with STATE_LOCK:
                if STATE["running"]:
                    CANCEL_EVENT.set()
                    STATE["logs"].append("🛑 キャンセル要求を送信しました。")
            self.send_response(204)
            self.end_headers()
            return
        if self.path != "/run":
            self.send_error(404)
            return
        try:
            form = parse_form(self)
            start_workflow(form)
        except (UnicodeDecodeError, RuntimeError, ValueError) as error:
            self.send_html(render_page(str(error)), status=400)
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def send_html(self, content, status=200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AI Dev Orchestrator listening on http://{HOST}:{PORT}")
    server.serve_forever()
