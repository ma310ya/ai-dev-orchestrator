import sys
import requests
from bs4 import BeautifulSoup
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.text_rank import TextRankSummarizer

def fetch_webpage(url, retries=3):
    """
    指定されたURLからWebページを取得する（リトライ機能付き）
    """
    headers = {"User-Agent": "WebPageSummaryApp/1.0"}
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.text
            else:
                raise Exception(f"HTTP Status {response.status_code}")
        except requests.RequestException as e:
            if i == retries - 1:
                return {"error": str(e)}
            wait_time = 2 ** i
            print(f"  [!] Webページの取得に失敗しました。{wait_time}秒後に再試行します... ({i+1}/{retries})")
            time.sleep(wait_time)
    return {"error": "不明なエラー"}

def extract_text_from_html(html):
    """
    HTMLからテキストコンテンツを抽出する
    """
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.string if soup.title else "タイトル不明"
    # 主に本文と思われる部分を抽出
    text = ' '.join([p.get_text() for p in soup.find_all('p')])
    return title, text

def summarize_text(text, language="japanese", sentence_count=3):
    """
    テキストを要約する（TextRankによる要約）
    """
    parser = PlaintextParser.from_string(text, Tokenizer(language))
    summarizer = TextRankSummarizer()
    summary = summarizer(parser.document, sentence_count)
    return "\n  - " + "\n  - ".join(str(sentence) for sentence in summary)

def main():
    """
    メイン処理
    """
    if len(sys.argv) < 2:
        print("使用方法: python generated_app.py <URL>")
        sys.exit(1)

    url = sys.argv[1]
    print("\n【 Webページ要約結果 】")
    print("----------------------------------------------")

    # Webページの取得
    html = fetch_webpage(url)
    if isinstance(html, dict) and "error" in html:
        print(f"エラー: {html['error']}")
        sys.exit(1)

    # ページタイトルと本文抽出
    title, text = extract_text_from_html(html)

    # 要約生成
    if not text.strip():
        print("エラー: 抽出した本文が空です。")
        sys.exit(1)

    summary = summarize_text(text)

    # 結果の出力
    print(f"タイトル: {title}")
    print("要約:")
    print(summary)
    print("----------------------------------------------")

if __name__ == "__main__":
    main()