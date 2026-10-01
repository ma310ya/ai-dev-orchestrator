import urllib.request
import urllib.error
import json
import time
import unicodedata

# =================================================================
# 設定値
# =================================================================

# 都市設定（緯度・経度）
CITIES = {
    "東京": {"lat": 35.6895, "lon": 139.6917},
    "大阪": {"lat": 34.6937, "lon": 135.5023},
    "福岡": {"lat": 33.5902, "lon": 130.4017}
}

# WMO気象コードの変換テーブル
# 参照: https://open-meteo.com/en/docs
WEATHER_CODES = {
    0: "晴天",
    1: "ほぼ晴れ", 2: "時々曇り", 3: "曇り",
    45: "霧", 48: "着氷性の霧",
    51: "軽度の霧雨", 53: "中程度の霧雨", 55: "濃い霧雨",
    61: "小雨", 63: "中程度の雨", 65: "強い雨",
    71: "小雪", 73: "中程度の雪", 75: "強い雪",
    80: "にわか雨", 81: "強いにわか雨", 82: "猛烈なにわか雨",
    95: "雷雨",
}

# =================================================================
# 関数定義
# =================================================================

def get_display_width(text):
    """
    全角文字を2、半角文字を1として文字列の表示幅を計算する
    """
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ('F', 'W', 'A'):
            width += 2
        else:
            width += 1
    return width

def pad_text(text, target_width):
    """
    表示幅を考慮してパディングを行う
    """
    current_width = get_display_width(text)
    padding = target_width - current_width
    return text + " " * max(0, padding)

def fetch_weather(city_name, lat, lon, retries=3):
    """
    Open-Meteo APIから気象データを取得する（リトライ機能付き）
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&current_weather=true&timezone=Asia%2FTokyo"
    )
    
    # ユーザーエージェントを設定（一部の環境でAPIアクセスを安定させるため）
    headers = {"User-Agent": "WeatherChecker/1.0"}
    req = urllib.request.Request(url, headers=headers)

    for i in range(retries):
        try:
            # タイムアウトを10秒に設定
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.getcode() == 200:
                    return json.loads(response.read().decode('utf-8'))
                else:
                    raise Exception(f"HTTP Status {response.getcode()}")
                    
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            if i == retries - 1:
                return {"error": str(e)}
            
            # 指数バックオフ: 1秒, 2秒, 4秒...と待機時間を増やす
            wait_time = 2 ** i
            print(f"  [!] {city_name}の取得に失敗しました。{wait_time}秒後に再試行します... ({i+1}/{retries})")
            time.sleep(wait_time)
            
    return {"error": "不明なエラー"}

def main():
    """
    メイン処理
    """
    print("\n【 現在の気象情報取得中 (Open-Meteo API) 】")
    print("-" * 65)
    
    # ヘッダーの表示
    header = (
        f"{pad_text('都市', 8)} | "
        f"{pad_text('天気', 12)} | "
        f"{pad_text('気温', 8)} | "
        f"{pad_text('取得時刻', 19)}"
    )
    print(header)
    print("-" * 65)
    
    for name, pos in CITIES.items():
        data = fetch_weather(name, pos['lat'], pos['lon'])
        
        if "current_weather" in data:
            curr = data["current_weather"]
            
            # データの整形
            temp = f"{curr['temperature']:.1f} °C"
            time_str = curr['time'].replace('T', ' ')
            weather_desc = WEATHER_CODES.get(curr['weathercode'], f"不明({curr['weathercode']})")
            
            # 結果表示
            line = (
                f"{pad_text(name, 8)} | "
                f"{pad_text(weather_desc, 12)} | "
                f"{temp:>8} | "
                f"{time_str:<19}"
            )
            print(line)
        else:
            error_msg = data.get("error", "データ取得失敗")
            print(f"{pad_text(name, 8)} | エラー: {error_msg}")
            
    print("-" * 65)
    print("※データ提供: Open-Meteo (https://open-meteo.com/)\n")

# =================================================================
# 実行
# =================================================================

if __name__ == "__main__":
    main()