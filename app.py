import os
import glob

import sys

# Python標準ライブラリ名のリスト（除外用）
STDLIB_MODULES = set(sys.builtin_module_names) | {
    "os", "sys", "time", "re", "math", "json", "glob", "shutil", "pathlib",
    "datetime", "subprocess", "random", "collections", "itertools", "functools",
    "typing", "typing_extensions", "unittest", "logging", "hashlib", "io",
    "tempfile", "traceback", "copy", "threading", "multiprocessing", "queue",
    "socket", "http", "urllib", "email", "csv", "sqlite3", "base64", "uuid"
}

# パッケージ名とインポート名が異なるもののマッピング
PACKAGE_MAPPING = {
    "cv2": "opencv-python-headless",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn"
}

def extract_pip_packages(code: str) -> list[str]:
    import ast
    packages = set()
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0]
                    if pkg and pkg not in STDLIB_MODULES:
                        packages.add(PACKAGE_MAPPING.get(pkg, pkg))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split(".")[0]
                    if pkg and pkg not in STDLIB_MODULES:
                        packages.add(PACKAGE_MAPPING.get(pkg, pkg))
    except Exception:
        pass
    return sorted(list(packages))

import streamlit as st
from main import app as dev_workflow, get_available_gemini_models, set_runtime_callbacks

st.set_page_config(page_title="AI Dev Orchestrator", page_icon="🤖", layout="wide")
st.title("🤖 AI開発自律オーケストレーター")

# カスタムスタイル
st.markdown("""
<style>
button[kind="primary"] {
    background-color: #10b981 !important;
    border-color: #10b981 !important;
    color: #ffffff !important;
    font-weight: 600 !important;
}
button[kind="primary"]:hover {
    background-color: #059669 !important;
    border-color: #059669 !important;
}
button[kind="secondary"] {
    border-color: #ef4444 !important;
    color: #ef4444 !important;
}
button[kind="secondary"]:hover {
    background-color: #fef2f2 !important;
    border-color: #dc2626 !important;
    color: #dc2626 !important;
}
</style>
""", unsafe_allow_html=True)

# セッション状態管理
if "saved_spec" not in st.session_state:
    st.session_state.saved_spec = ""
if "cancel_requested" not in st.session_state:
    st.session_state.cancel_requested = False
if "last_request_text" not in st.session_state:
    st.session_state.last_request_text = ""

# --- サイドバー設定パネル ---
st.sidebar.header("⚙️ パイプライン設定")

default_repo = os.environ.get("GITHUB_REPO", "chiharumakino7-create/ai-dev-orchestrator")
repo_input = st.sidebar.text_input("📁 対象GitHubリポジトリ", value=default_repo)

real_gemini_models = get_available_gemini_models()
other_models = [
    "gpt-4o",
    "gpt-4o-mini",
    "o3-mini",
    "anthropic/claude-3-5-sonnet-20241022",
    "groq/llama-3.3-70b-versatile",
    "✏️ 手動でモデル名を入力..."
]
available_options = real_gemini_models + other_models

st.sidebar.subheader("🧠 モデル選択")

plan_choice = st.sidebar.selectbox("📋 要件分析・仕様策定 (Plan)", available_options, index=0)
plan_model = st.sidebar.text_input("Planモデル名 (LiteLLM形式)", value="gemini/gemini-3.8-flash") if plan_choice == "✏️ 手動でモデル名を入力..." else plan_choice

code_choice = st.sidebar.selectbox("💻 コード生成・修正 (Code)", available_options, index=0)
code_model = st.sidebar.text_input("Codeモデル名 (LiteLLM形式)", value="gemini/gemini-3.8-flash") if code_choice == "✏️ 手動でモデル名を入力..." else code_choice

st.sidebar.markdown("---")
st.sidebar.caption("💡 実行中の詳細ステータスはライブウィンドウでリアルタイム監視できます。")

# --- メイン画面：対象ファイル選択 ---
st.subheader("🎯 開発対象ファイルの選択")

py_files = sorted([f for f in glob.glob("*.py") if f not in ["app.py", "main.py"]])
file_options = ["➕ 新規ファイルを作成..."] + py_files
selected_option = st.selectbox("対象ファイルを選択", file_options, index=0)

if selected_option == "➕ 新規ファイルを作成...":
    target_file = st.text_input("新規作成するファイル名 (.py)", value="new_tool.py")
    is_existing = False
else:
    target_file = selected_option
    is_existing = os.path.exists(target_file)

existing_code = ""
if is_existing:
    st.info(f"ℹ️ **【修正・改善パイプライン】** 既存ファイル `{target_file}` を読み込み、要件に合わせてリファクタリングします。")
    with open(target_file, "r", encoding="utf-8") as f:
        existing_code = f.read()
    with st.expander(f"📄 現在の `{target_file}` のコードを確認", expanded=False):
        st.code(existing_code, language="python")
else:
    st.warning(f"🆕 **【新規作成パイプライン】** `{target_file}` を新しくゼロから生成します。")

# --- 要件入力 ---
st.subheader("📝 要件・仕様の入力")

uploaded_file = st.file_uploader("📎 要件定義・仕様書ファイル (.md / .txt) がある場合はアップロード", type=["md", "txt"])

initial_text = ""
if uploaded_file is not None:
    try:
        initial_text = uploaded_file.read().decode("utf-8")
        st.success(f"✅ `{uploaded_file.name}` の内容を読み込みました！")
    except Exception as e:
        st.error(f"読み込み失敗: {e}")

instruction_label = "要件・指示・エラーログ" if not is_existing else "改修要件・追加したい機能・エラーログ"
user_request = st.text_area(instruction_label, value=initial_text, height=160, placeholder="ここに要望を入力してください。")

# 新しい指示が入力された場合、古い仕様を自動リセットして再策定させる
if user_request.strip() and user_request.strip() != st.session_state.last_request_text:
    if st.session_state.saved_spec:
        st.session_state.saved_spec = ""
    st.session_state.last_request_text = user_request.strip()


# --- 保存済み仕様書の表示＆手直し枠 ---
if st.session_state.saved_spec:
    st.success("💾 **仕様策定（Plan）は完了・保持されています！** （手動編集も可能です）")
    with st.expander("📋 策定済み仕様書を確認・手動編集", expanded=True):
        st.session_state.saved_spec = st.text_area("仕様書内容", value=st.session_state.saved_spec, height=180)
        if st.button("🗑️ 仕様をクリアして最初から作り直す"):
            st.session_state.saved_spec = ""
            st.rerun()

# --- 実行ボタンエリア ---
col_run, col_cancel = st.columns([2, 1])

with col_cancel:
    if st.button("🛑 処理をキャンセル", type="secondary", use_container_width=True):
        st.session_state.cancel_requested = True
        st.warning("⚠️ キャンセル要求を送信しました...")

button_label = "🚀 コード生成から再開・実行" if st.session_state.saved_spec else "🚀 開発パイプラインを実行"

with col_run:
    run_btn = st.button(button_label, type="primary", use_container_width=True)

if run_btn:
    if not user_request.strip() and not st.session_state.saved_spec:
        st.error("要件または指示を入力してください！")
    elif not target_file.endswith(".py"):
        st.error("ファイル名は .py で終わる必要があります！")
    else:
        st.session_state.cancel_requested = False

        with st.status("🚀 パイプラインを起動しています...", expanded=True) as status_window:
            execution_logs = []
            
            def log_status(msg: str):
                execution_logs.append(msg)
                status_window.write(msg)
                if "【仕様策定" in msg:
                    status_window.update(label="📋 仕様策定フェーズを実行中...", state="running")
                elif "【コード生成" in msg:
                    status_window.update(label="💻 コード生成フェーズを実行中...", state="running")
                elif "【構文テスト" in msg:
                    status_window.update(label="🧪 構文テストを実行中...", state="running")
                elif "【Git同期" in msg:
                    status_window.update(label="🚀 GitHub同期を実行中...", state="running")

            def check_cancellation():
                return st.session_state.get("cancel_requested", False)

            # コールバックを実行時コンテキストに登録（Stateには渡さない）
            set_runtime_callbacks(status_cb=log_status, cancel_fn=check_cancellation)

            thread_config = {
                "configurable": {"thread_id": f"dev_{target_file}"},
                "recursion_limit": 15
            }

            try:
                # 純粋なプリミティブ型のみをinvokeに渡す
                result = dev_workflow.invoke({
                    "request": user_request,
                    "target_file": target_file,
                    "existing_code": existing_code,
                    "plan_model": plan_model,
                    "code_model": code_model,
                    "github_repo": repo_input,
                    "spec": st.session_state.saved_spec,
                    "code": "",
                    "test_result": "",
                    "iteration": 0,
                    "is_success": False
                }, config=thread_config)

                if result.get("spec"):
                    st.session_state.saved_spec = result.get("spec")

                err_msg = result.get("test_result", "")

                err_msg = result.get("test_result", "")
                if err_msg and not result.get("is_success"):
                    execution_logs.append(f"❌ エラー内容: {err_msg}")

                # 実行ログ全体（エラー詳細含む）をコピーできる枠を生成
                if execution_logs:
                    status_window.caption("📋 実行ログ・エラー詳細（右上アイコンからワンクリックで全体コピー可能）:")
                    status_window.code("\n".join(execution_logs), language="text")

                if result.get("is_success"):
                    status_window.update(label="🎉 すべての開発工程が正常に完了しました！", state="complete", expanded=False)
                    st.success(f"🎉 `{target_file}` の自律生成・テスト・コミットが完了しました！")
                    
                    code_content = result.get("code", "")
                    needed_pkgs = extract_pip_packages(code_content)
                    
                    st.markdown("### 🚀 すぐに動かすためのコマンド")
                    if needed_pkgs:
                        pip_cmd = f"pip install " + " ".join(needed_pkgs)
                        st.caption("📦 必要な外部パッケージのインストール：")
                        st.code(pip_cmd, language="bash")
                    else:
                        st.caption("📦 外部パッケージのインストールは不要です（標準ライブラリのみ）。")
                    
                    st.caption("▶️ アプリ・スクリプトの実行：")
                    st.code(f"python3 {target_file}", language="bash")

                    st.subheader("📋 策定された仕様")
                    st.markdown(result.get("spec"))
                    st.subheader("💻 反映されたコード")
                    st.code(code_content, language="python")
                elif "キャンセル" in err_msg:
                    status_window.update(label="🛑 処理が中断されました", state="error", expanded=True)
                    st.warning(f"🛑 {err_msg}")
                elif err_msg.startswith("【"):
                    status_window.update(label="⚠️ エラーにより一時中断しました", state="error", expanded=True)
                    st.warning(f"{err_msg}\n\n💡 **仕様（Plan）は保存されています。サイドバーで別のモデルを選択し、「再開」ボタンを押せば続きからコード生成を再開できます。**")
                else:
                    status_window.update(label="❌ パイプラインが失敗しました", state="error", expanded=True)
                    st.error("❌ パイプラインが失敗しました。")
                    
                    # 1. 生のエラーログ
                    st.code(err_msg, language="bash")
                    
                    # 2. そのままAIへ指示できる整形済みプロンプトの作成
                    debug_prompt = f"""以下のファイルでエラーが発生しました。修正してください。
【対象ファイル】: {target_file}
【発生エラー】:
{err_msg}

【改修要件】:
上記エラーの原因を特定し、安全に動作するようにコードを修正してください。"""
                    
                    st.markdown("### 🛠️ デバッグ支援・ショートカット")
                    
                    # コピーしやすいコードブロック
                    st.caption("📋 下の枠内（右上アイコン）からワンクリックでAIへの修正指示プロンプトをコピーできます：")
                    st.code(debug_prompt, language="markdown")
                    
                    # ターミナル検証用コマンド
                    st.caption("💻 ターミナルで手動検証するコマンド：")
                    test_cmd = f"python3 -m py_compile {target_file} && python3 {target_file}"
                    st.code(test_cmd, language="bash")

            except Exception as e:
                execution_logs.append(f"❌ 予期しない例外: {e}")
                status_window.caption("📋 実行ログ・エラー詳細（右上アイコンからワンクリックで全体コピー可能）:")
                status_window.code("\n".join(execution_logs), language="text")
                status_window.update(label="❌ 予期しないエラーが発生しました", state="error", expanded=True)
                st.error(f"❌ 実行エラー: {e}")
            finally:
                set_runtime_callbacks(None, None)
