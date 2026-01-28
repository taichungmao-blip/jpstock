import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import requests
import os
from datetime import datetime, timedelta

# ==========================================
# 1. 設定區域 (Configuration)
# ==========================================

# 監控清單 (分為兩組以便報告)
TARGETS_PRECIOUS = {
    "GC=F": "黃金期貨",
    "SI=F": "白銀期貨",
    "00635U.TW": "元大S&P黃金",
    "9955.TW": "佳龍"
}

TARGETS_INDUSTRIAL = {
    "HG=F": "銅期貨 (Dr.Copper)",
    "0358.HK": "江西銅業 (港)",
    # "2009.TW": "第一銅" # 如果需要可自行加入
}

# 輔助指標
TARGETS_INDEX = {
    "DX-Y.NYB": "美元指數"
}

# 合併所有 Ticker 用於下載
ALL_TARGETS = {**TARGETS_PRECIOUS, **TARGETS_INDUSTRIAL, **TARGETS_INDEX}

# 監控天數
LOOKBACK_DAYS = 180

# Discord Webhook
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# ==========================================
# 2. 技術指標與數據
# ==========================================

def calculate_rsi(series, period=14):
    """計算 RSI"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_market_data():
    """下載數據"""
    start_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS + 60)).strftime('%Y-%m-%d')
    tickers = list(ALL_TARGETS.keys())
    print(f"下載數據中... {tickers}")
    
    # 下載並填補空值
    data = yf.download(tickers, start=start_date, progress=False)['Close']
    data = data.ffill()
    return data

# ==========================================
# 3. 策略判讀核心
# ==========================================

def analyze_single_target(df, code):
    """單一標的 RSI 與漲跌分析"""
    try:
        if code not in df.columns: return None
        prices = df[code]
        current_price = prices.iloc[-1]
        prev_price = prices.iloc[-2]
        change_pct = ((current_price - prev_price) / prev_price) * 100
        
        # RSI
        rsi_series = calculate_rsi(prices)
        current_rsi = rsi_series.iloc[-1]
        
        # 狀態 Icon
        if change_pct > 2.5: status_icon = "🔥" # 大漲
        elif change_pct < -2.5: status_icon = "❄️" # 大跌
        elif change_pct > 0: status_icon = "📈"
        else: status_icon = "📉"
        
        # RSI 註解
        rsi_note = ""
        if current_rsi > 75: rsi_note = "⚠️過熱"
        elif current_rsi > 55: rsi_note = "💪強勢"
        elif current_rsi < 30: rsi_note = "✨超賣"
        else: rsi_note = "➡️盤整"
            
        return {
            "price": current_price,
            "change": change_pct,
            "rsi": current_rsi,
            "icon": status_icon,
            "note": rsi_note
        }
    except Exception:
        return None

def analyze_rotation_logic(df):
    """分析板塊輪動邏輯 (金 vs 銅)"""
    try:
        gold = df['GC=F']
        copper = df['HG=F']
        
        # 1. 30天相關係數 (Correlation)
        # 如果相關係數降低，代表走勢脫鉤，可能是輪動開始
        correlation = gold.rolling(30).corr(copper).iloc[-1]
        
        # 2. 金銅比 (Gold / Copper Ratio)
        # 金銅比下跌通常代表經濟復甦/通膨預期 (銅強於金)
        ratio = gold / copper
        current_ratio = ratio.iloc[-1]
        prev_ratio = ratio.iloc[-20] # 一個月前
        ratio_trend = "↘️下降(利好工業)" if current_ratio < prev_ratio else "↗️上升(避險主導)"
        
        # 3. 輪動訊號判斷
        msg = ""
        if correlation < 0.3:
            msg += "⚡ **注意：金銅走勢脫鉤 (相關性低)**，留意資金輪動。\n"
        
        msg += f"⚖️ **金銅比**: `{current_ratio:.1f}` ({ratio_trend})\n"
        
        return msg
    except Exception as e:
        return f"無法計算輪動數據: {e}\n"

def plot_chart(df):
    """繪製 黃金 vs 銅 vs 庫存股"""
    plt.figure(figsize=(10, 6))
    plt.style.use('bmh') # 使用乾淨的樣式
    
    # 正規化 (以第一天為 100)
    norm_df = (df / df.iloc[0]) * 100
    
    # 畫線
    plt.plot(norm_df.index, norm_df['GC=F'], label='Gold (Safe)', color='#FFD700', linewidth=2)
    plt.plot(norm_df.index, norm_df['HG=F'], label='Copper (Industrial)', color='#C15436', linewidth=2.5)
    
    # 如果有抓到江西銅業，也畫出來
    if '0358.HK' in norm_df.columns:
         plt.plot(norm_df.index, norm_df['0358.HK'], label='Jiangxi Copper (0358)', color='blue', alpha=0.6, linestyle='--')

    plt.title(f"Rotation Watch: Gold vs Copper ({LOOKBACK_DAYS} Days)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    img_path = "rotation_chart.png"
    plt.savefig(img_path, dpi=100, bbox_inches='tight')
    plt.close()
    return img_path

def send_discord_notify(msg, img_path=None):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 Webhook，只印出訊息")
        print(msg)
        return
    
    # Discord 限制 embed 內容長度，這裡用純文字簡單發送
    # 如果要漂亮可以使用 Embed Object，但純文字+圖片最穩
    data = {"content": msg}
    files = {}
    
    if img_path and os.path.exists(img_path):
        files = {"file": (os.path.basename(img_path), open(img_path, "rb"))}
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, data=data, files=files)
        print("✅ Discord 通知發送成功")
    except Exception as e:
        print(f"發送失敗: {e}")
    finally:
        if files: files["file"][1].close()

# ==========================================
# 4. 主程式
# ==========================================

def main():
    try:
        df = get_market_data()
        if df.empty: return
        
        date_str = df.index[-1].strftime('%Y-%m-%d')
        
        # --- 組合訊息 ---
        msg = f"## 🛠️ 金屬板塊輪動追蹤 `{date_str}`\n"
        
        # 1. 宏觀與輪動分析
        msg += "### 🌏 宏觀視野\n"
        msg += analyze_rotation_logic(df)
        dxy = analyze_single_target(df, 'DX-Y.NYB')
        if dxy:
            msg += f"💵 **美元指數**: `{dxy['price']:.2f}` ({dxy['change']:+.2f}%) | {dxy['note']}\n"
        
        # 2. 貴金屬區塊
        msg += "\n### 🥇 貴金屬 (避險/貨幣)\n"
        for code, name in TARGETS_PRECIOUS.items():
            res = analyze_single_target(df, code)
            if res:
                msg += f"> **{name}** `{res['price']:.2f}` {res['icon']} ({res['change']:+.2f}%) | RSI:{res['rsi']:.0f}\n"

        # 3. 工業金屬區塊
        msg += "\n### 🏭 工業金屬 (製造/通膨)\n"
        for code, name in TARGETS_INDUSTRIAL.items():
            res = analyze_single_target(df, code)
            if res:
                msg += f"> **{name}** `{res['price']:.2f}` {res['icon']} ({res['change']:+.2f}%) | RSI:{res['rsi']:.0f}\n"
        
        # 策略提醒
        msg += "\n💡 *觀點：若黃金盤整但銅價獨強，關注資源股補漲行情。*"

        # 產生圖表並發送
        img_path = plot_chart(df)
        send_discord_notify(msg, img_path)

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
