import base64
import os
import re
import time
import subprocess
import tempfile
from pathlib import Path
import requests
import litellm
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict, Callable, Optional
from credentials import delete_credential, get_credential, save_credential


COPILOT_CLIENT_ID = os.environ.get(
    "GITHUB_COPILOT_CLIENT_ID", "Iv1.b507a08c87ecfe98"
)

def get_github_token():
    credential = get_credential("github")
    return credential["value"] if credential else None

def get_gemini_api_key():
    credential = get_credential("gemini")
    return credential["value"] if credential else None

def get_copilot_token():
    credential = get_credential("copilot")
    return credential["value"] if credential else None

def validate_gemini_api_key(api_key: str):
    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": api_key},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        status = getattr(error.response, "status_code", None)
        detail = f" (HTTP {status})" if status else ""
        raise ValueError(f"Gemini APIキーを確認できませんでした{detail}。") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Gemini APIからモデル一覧を取得できませんでした。")
    return True

def save_gemini_api_key(api_key: str):
    validate_gemini_api_key(api_key)
    save_credential("gemini", api_key)

def github_oauth_client_id():
    return os.environ.get(
        "GITHUB_OAUTH_CLIENT_ID", "Ov23lix5FI3bxTu1PV2N"
    ).strip()

def get_writable_github_repositories():
    token = get_github_token()
    if not token:
        raise RuntimeError(
            "GitHub認証が見つかりません。認証設定画面からログインしてください。"
        )

    repositories = []
    page = 1
    while True:
        try:
            response = requests.get(
                "https://api.github.com/user/repos",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params={
                    "affiliation": "owner,collaborator,organization_member",
                    "visibility": "all",
                    "per_page": 100,
                    "page": page,
                },
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            if getattr(error.response, "status_code", None) == 401:
                delete_credential("github")
                raise RuntimeError(
                    "GitHub認証が無効または期限切れです。認証設定画面から再ログインしてください。"
                ) from error
            raise RuntimeError(f"GitHubリポジトリ一覧を取得できませんでした: {error}") from error

        try:
            page_repositories = response.json()
        except ValueError as error:
            raise RuntimeError("GitHubからリポジトリ一覧を読み取れませんでした。") from error
        if not isinstance(page_repositories, list):
            raise RuntimeError("GitHubから予期しないリポジトリ一覧が返されました。")
        repositories.extend(
            {
                "full_name": repository["full_name"],
                "default_branch": repository["default_branch"],
            }
            for repository in page_repositories
            if repository.get("permissions", {}).get("push")
            and repository.get("full_name")
            and repository.get("default_branch")
        )
        if len(page_repositories) < 100:
            return repositories
        page += 1

def get_writable_github_repository(full_name: str):
    for repository in get_writable_github_repositories():
        if repository["full_name"] == full_name:
            return repository
    raise ValueError("選択したリポジトリに書き込み権限がありません。")

class CancellationError(Exception):
    pass

# 実行時コールバック管理用（Stateから分離してmsgpackエラーを防止）
CURRENT_STATUS_CB: Optional[Callable[[str], None]] = None
CURRENT_CANCEL_FN: Optional[Callable[[], bool]] = None

def set_runtime_callbacks(status_cb=None, cancel_fn=None):
    global CURRENT_STATUS_CB, CURRENT_CANCEL_FN
    CURRENT_STATUS_CB = status_cb
    CURRENT_CANCEL_FN = cancel_fn

def get_available_gemini_models():
    api_key = get_gemini_api_key()
    if not api_key:
        return ["gemini/gemini-3.8-flash"]
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        res = requests.get(
            url, headers={"x-goog-api-key": api_key}, timeout=5
        )
        if res.status_code == 200:
            models = []
            excluded = ["2.5", "tts", "image", "audio", "transcribe", "lyria", "robotics", "computer-use", "banana"]
            for m in res.json().get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    name = m.get("name", "").replace("models/", "")
                    if not any(k in name.lower() for k in excluded):
                        models.append(f"gemini/{name}")
            if models:
                def sort_priority(item):
                    if "3.8-flash" in item: return 0
                    if "flash-latest" in item: return 1
                    if "pro-latest" in item: return 2
                    if "3.7-flash" in item: return 3
                    return 10
                models.sort(key=lambda x: (sort_priority(x), x))
                return models
    except Exception as e:
        print(f"モデル一覧取得失敗: {e}")
    return ["gemini/gemini-3.8-flash"]

def normalize_model_name(model: str) -> str:
    if model.startswith("claude-") and not model.startswith("anthropic/"):
        return f"anthropic/{model}"
    return model

def resolve_api_key(model_name: str):
    if model_name.startswith("gemini/"):
        return get_gemini_api_key()
    return None

def call_llm(prompt, model="gemini/gemini-3.8-flash", max_retries=5):
    model = normalize_model_name(model)
    api_key = resolve_api_key(model)
    copilot_token = get_copilot_token() if model.startswith("github_copilot/") else None
    
    if model.startswith("gemini/") and not api_key:
        raise RuntimeError("【認証が必要】Gemini APIキーを認証設定画面で登録してください。")
    if model.startswith("github_copilot/") and not copilot_token:
        raise RuntimeError("【認証が必要】GitHub Copilotを認証設定画面で接続してください。")
    if not model.startswith(("gemini/", "github_copilot/")):
        raise RuntimeError("このアプリで利用できるAIプロバイダーはGeminiとGitHub Copilotのみです。")

    def completion():
        if not copilot_token:
            return litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
            )

        token_environment = {
            "GITHUB_COPILOT_TOKEN_DIR": None,
            "GITHUB_COPILOT_ACCESS_TOKEN_FILE": None,
            "GITHUB_COPILOT_API_KEY_FILE": None,
        }
        previous_environment = {
            name: os.environ.get(name) for name in token_environment
        }
        try:
            with tempfile.TemporaryDirectory(prefix="ai-orchestrator-copilot-") as token_dir:
                token_path = Path(token_dir) / "access-token"
                token_path.write_text(copilot_token, encoding="utf-8")
                try:
                    token_path.chmod(0o600)
                except OSError:
                    pass
                os.environ.update(
                    {
                        "GITHUB_COPILOT_TOKEN_DIR": token_dir,
                        "GITHUB_COPILOT_ACCESS_TOKEN_FILE": "access-token",
                        "GITHUB_COPILOT_API_KEY_FILE": "api-key.json",
                    }
                )
                return litellm.completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
        finally:
            for name, value in previous_environment.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    delay = 3
    for attempt in range(max_retries):
        if CURRENT_CANCEL_FN and CURRENT_CANCEL_FN():
            raise CancellationError("ユーザーにより処理がキャンセルされました。")

        try:
            if CURRENT_STATUS_CB:
                if attempt > 0:
                    CURRENT_STATUS_CB(f"🔄 [{model}] 試行中 ({attempt + 1}/{max_retries})...")
                else:
                    CURRENT_STATUS_CB(f"🧠 [{model}] 応答生成中...")

            res = completion()
            if res and hasattr(res, "choices") and len(res.choices) > 0:
                return res
        except Exception as e:
            if isinstance(e, CancellationError):
                raise e
            error_text = str(e)
            for secret in (api_key, copilot_token):
                if secret:
                    error_text = error_text.replace(secret, "[REDACTED]")
            err_str = error_text.lower()
            if "401" in err_str and model.startswith("github_copilot/"):
                delete_credential("copilot")
                raise RuntimeError(
                    "【認証が必要】GitHub Copilotの認証が無効または期限切れです。認証設定画面から再接続してください。"
                ) from e
            if model.startswith("gemini/") and (
                "401" in err_str or "api_key_invalid" in err_str or "api key not valid" in err_str
            ):
                delete_credential("gemini")
                raise RuntimeError(
                    "【認証が必要】Gemini APIキーが無効または期限切れです。認証設定画面から再登録してください。"
                ) from e
            
            # 日次上限到達は待機せず即時中断
            if ("429" in err_str or "quota" in err_str or "resource_exhausted" in err_str) and ("perday" in err_str or "freetier" in err_str or "daily" in err_str):
                raise RuntimeError(f"【1日の無料枠上限到達 (429)】`{model}` の本日のFree枠(20回)を使い切りました。別モデルを選択するか、課金設定(Billing)を有効にしてください。")

            is_busy = "503" in err_str or "high demand" in err_str or "service unavailable" in err_str
            is_rpm = "429" in err_str or "quota" in err_str

            if attempt < max_retries - 1:
                reason = "混雑(503)" if is_busy else "一時レート制限(429)"
                wait_msg = f"⏳ [{model}] {reason}を検知。{delay}秒待機して再試行します (次回: {attempt+2}/{max_retries}回目)"
                print(wait_msg)
                if CURRENT_STATUS_CB:
                    CURRENT_STATUS_CB(wait_msg)
                
                for _ in range(delay):
                    if CURRENT_CANCEL_FN and CURRENT_CANCEL_FN():
                        raise CancellationError("ユーザーにより処理がキャンセルされました。")
                    time.sleep(1)
                delay = min(delay * 2, 25)
            else:
                if is_busy:
                    prefix = "【サーバー混雑 (503)】"
                elif is_rpm:
                    prefix = "【短時間リクエスト上限 (429)】"
                elif "404" in err_str or "not found" in err_str:
                    prefix = "【モデル未提供 (404)】"
                else:
                    prefix = "【API呼び出しエラー】"
                raise RuntimeError(f"{prefix} ({model}): {error_text}")

    raise RuntimeError(f"API呼び出しに失敗しました: {model}")

# Stateは文字列・数値・真偽値などシリアライズ可能な基本型のみに限定
class DevState(TypedDict):
    request: str
    target_file: str
    existing_code: str
    plan_model: str
    code_model: str
    github_repo: str
    github_branch: str
    spec: str
    code: str
    test_result: str
    iteration: int
    is_success: bool

def plan_node(state: DevState):
    if state.get("spec") and state["spec"].strip():
        print("ℹ️ [Plan] 既存の策定済み仕様を再利用します。")
        if CURRENT_STATUS_CB:
            CURRENT_STATUS_CB("📋 **【仕様策定 (Plan)】** 保存済みの仕様書を再利用してスキップします。")
        return {"test_result": ""}

    model = state.get("plan_model") or "gemini/gemini-3.8-flash"
    print(f"📋 [Plan] 仕様策定中 (使用モデル: {model})...")
    if CURRENT_STATUS_CB:
        CURRENT_STATUS_CB(f"📋 **【仕様策定 (Plan)】** 要件を分析し、仕様書をまとめています... (Model: `{model}`)")
    
    context = f"開発対象ファイル: {state.get("target_file")}\n"
    if state.get("existing_code"):
        context += f"\n【既存コード】:\n{state["existing_code"]}\n"

    prompt = f"以下の要望を踏まえ、作成または修正・改善する単一ファイルPythonコードの仕様書を簡潔にまとめてください。\n\n{context}\n【要望・指示】:\n{state["request"]}"
    try:
        res = call_llm(prompt, model=model)
        return {"spec": res.choices[0].message.content, "test_result": ""}
    except Exception as e:
        return {"is_success": False, "test_result": str(e)}

def code_node(state: DevState):
    model = state.get("code_model") or "gemini/gemini-3.8-flash"
    current_iter = state.get("iteration", 0) + 1
    print(f"💻 [Code] コード生成・改修中 (試行: {current_iter}, 使用モデル: {model})...")
    if CURRENT_STATUS_CB:
        CURRENT_STATUS_CB(f"💻 **【コード生成 (Code)】** 仕様に基づきPythonコードを作成中... (試行: {current_iter}/3, Model: `{model}`)")
    
    if state.get("existing_code"):
        base_info = f"【既存コード】:\n{state["existing_code"]}\n\n【改修指示/エラー内容】:\n{state["request"]}"
        prompt = f"仕様と要望を元に、既存コードを改修した完全な単一ファイルPythonコードのみを出力してください。挨拶や前置き、解説文、要望の復唱は一切含めず、必ず ```python と ``` で囲んでください。\n仕様:\n{state.get("spec", "")}\n\n{base_info}"
    else:
        prompt = f"仕様を元に完全な単一ファイルPythonコードを出力してください。挨拶や前置き、解説文は一切含めず、必ず ```python と ``` で囲んでください。\n仕様:\n{state.get("spec", "")}"
    
    if state.get("test_result") and not state.get("is_success"):
        prompt += f"\n\n前回のテストで以下の構文エラーが発生しました。必ず修正してください:\n{state["test_result"]}"
    
    try:
        res = call_llm(prompt, model=model)
    except Exception as e:
        return {"is_success": False, "test_result": str(e), "iteration": current_iter}

    raw_code = res.choices[0].message.content
    matches = re.findall(r"```(?:python)?\s*(.*?)\s*```", raw_code, re.DOTALL)
    if matches:
        clean_code = matches[0].strip()
    else:
        lines = raw_code.strip().splitlines()
        code_lines = []
        started = False
        for l in lines:
            if not started and (l.startswith("import ") or l.startswith("from ") or l.startswith("#") or l.startswith("def ")):
                started = True
            if started:
                code_lines.append(l)
        clean_code = "\n".join(code_lines).strip() if code_lines else raw_code.strip()

    return {"code": clean_code, "test_result": "", "iteration": current_iter}

def test_node(state: DevState):
    code_text = state.get("code", "")
    if not code_text:
        return {"test_result": "コードが生成されませんでした", "is_success": False}

    print("🧪 [Test] 構文チェック実行中...")
    if CURRENT_STATUS_CB:
        CURRENT_STATUS_CB("🧪 **【構文テスト (Test)】** `py_compile` による構文チェックを実行中...")

    target_file = state.get("target_file", "generated_app.py")
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(code_text)
    
    result = subprocess.run(["python3", "-m", "py_compile", target_file], capture_output=True, text=True)
    if result.returncode == 0:
        print("✅ 構文テスト通過！")
        return {"test_result": "PASS", "is_success": True}
    else:
        print("❌ 構文エラー検知:", result.stderr)
        return {"test_result": result.stderr, "is_success": False}

def commit_node(state: DevState):
    print("🚀 [Commit & Push] GitHubへ同期中...")
    if CURRENT_STATUS_CB:
        CURRENT_STATUS_CB("🚀 **【Git同期 (Commit & Push)】** GitHubリポジトリへコミット＆プッシュを実行中...")

    target_file = state.get("target_file", "generated_app.py")
    repo = state.get("github_repo")
    branch = state.get("github_branch") or "main"
    token = get_github_token()
    if not token or not repo:
        return {"is_success": False, "test_result": "GitHub Token or Repo is missing"}

    try:
        subprocess.run(["git", "config", "--global", "user.name", "ai-orchestrator"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "orchestrator@agent.local"], check=True)
        remote_url = f"https://github.com/{repo}.git"
        subprocess.run(["git", "remote", "set-url", "origin", remote_url], check=True)
        subprocess.run(["git", "add", target_file], check=True)
        commit_msg = f"feat/fix: auto update {target_file} by ai-orchestrator"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        git_env = os.environ.copy()
        credentials = base64.b64encode(
            f"x-access-token:{token}".encode("utf-8")
        ).decode("ascii")
        git_env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credentials}",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        subprocess.run(
            ["git", "pull", "--rebase", "origin", branch],
            check=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            check=True,
            env=git_env,
        )
        print("🎉 GitHubへの自動プッシュ完了！")
    except subprocess.CalledProcessError as e:
        print("⚠️ Git操作でエラー:", e)
    return {}

def route_from_plan(state: DevState):
    if not state.get("spec") or state.get("test_result"):
        return END
    return "code"

def route_from_code(state: DevState):
    if not state.get("code") or state.get("test_result"):
        return END
    return "test"

def route_from_test(state: DevState):
    if state.get("is_success"):
        return "commit"
    if state.get("iteration", 0) >= 3:
        print("🛑 最大リトライ回数到達。終了します。")
        return END
    return "code"

checkpointer = MemorySaver()

workflow = StateGraph(DevState)
workflow.add_node("plan", plan_node)
workflow.add_node("code", code_node)
workflow.add_node("test", test_node)
workflow.add_node("commit", commit_node)

workflow.set_entry_point("plan")
workflow.add_conditional_edges("plan", route_from_plan, {"code": "code", END: END})
workflow.add_conditional_edges("code", route_from_code, {"test": "test", END: END})
workflow.add_conditional_edges("test", route_from_test, {"commit": "commit", "code": "code", END: END})
workflow.add_edge("commit", END)

app = workflow.compile(checkpointer=checkpointer)
dev_workflow = app
