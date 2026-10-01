import os
import re
import time
import subprocess
import requests
import litellm
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict, Callable, Optional

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
DEFAULT_REPO = os.environ.get("GITHUB_REPO", "chiharumakino7-create/ai-dev-orchestrator")

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
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return ["gemini/gemini-3.8-flash"]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        res = requests.get(url, timeout=5)
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
    if "gemini" in model_name:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    elif "gpt" in model_name or "o1" in model_name or "o3" in model_name:
        return os.environ.get("OPENAI_API_KEY")
    elif "claude" in model_name or "anthropic" in model_name:
        return os.environ.get("ANTHROPIC_API_KEY")
    elif "groq" in model_name:
        return os.environ.get("GROQ_API_KEY")
    return None

def call_llm(prompt, model="gemini/gemini-3.8-flash", max_retries=5):
    model = normalize_model_name(model)
    api_key = resolve_api_key(model)
    
    if not api_key:
        if "anthropic" in model or "claude" in model:
            raise RuntimeError("【APIキー未設定】`ANTHROPIC_API_KEY` が設定されていません。")
        elif "gpt" in model or "o1" in model or "o3" in model:
            raise RuntimeError("【APIキー未設定】`OPENAI_API_KEY` が設定されていません。")
        elif "gemini" in model:
            raise RuntimeError("【APIキー未設定】`GEMINI_API_KEY` が設定されていません。")

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

            res = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key
            )
            if res and hasattr(res, "choices") and len(res.choices) > 0:
                return res
        except Exception as e:
            if isinstance(e, CancellationError):
                raise e
            err_str = str(e).lower()
            
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
                raise RuntimeError(f"{prefix} ({model}): {e}")

    raise RuntimeError(f"API呼び出しに失敗しました: {model}")

# Stateは文字列・数値・真偽値などシリアライズ可能な基本型のみに限定
class DevState(TypedDict):
    request: str
    target_file: str
    existing_code: str
    plan_model: str
    code_model: str
    github_repo: str
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
    repo = state.get("github_repo") or DEFAULT_REPO
    if not GITHUB_TOKEN or not repo:
        return {"is_success": False, "test_result": "GitHub Token or Repo is missing"}

    try:
        subprocess.run(["git", "config", "--global", "user.name", "ai-orchestrator"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "orchestrator@agent.local"], check=True)
        remote_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{repo}.git"
        subprocess.run(["git", "remote", "set-url", "origin", remote_url], check=True)
        subprocess.run(["git", "add", target_file], check=True)
        commit_msg = f"feat/fix: auto update {target_file} by ai-orchestrator"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
        subprocess.run(["git", "push", "origin", "main"], check=True)
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
