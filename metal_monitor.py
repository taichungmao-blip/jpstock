import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import requests
import os
from datetime import datetime, timedelta

# ==========================================
# 1. 設定區域 (Configuration)
# ==========================================

# 監控清單
TARGETS_PRECIOUS = {
    "GC=F": "黃金期貨",
    "SI=F": "白銀期貨",
    "00635U.TW": "元大S&P黃金",
    "9955.TW": "佳龍"
}

TARGETS_INDUSTRIAL = {
    "HG=F": "銅期貨 (Dr.Copper)",
    "0358.HK": "江西銅業 (港)",
    # "2009.TW": "第一銅" 
}

TARGETS_INDEX = {
    "DX-Y.NYB": "美元指數"
}

ALL_TARGETS = {**TARGETS_PRECIOUS, **TARGETS_INDUSTRIAL, **TARGETS_INDEX}

LOOKBACK_DAYS = 180
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
        if change_pct > 2.5: status_icon = "🔥" 
        elif change_pct < -2.5: status_icon = "❄️" 
        elif change_pct > 0: status_icon = "📈"
        else: status_icon = "📉"
        
        # RSI 註解 (調整為更直觀的文字)
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
        
        # 1. 30天相關係數
        correlation = gold.rolling(30).corr(copper).iloc[-1]
        
        # 2. 金銅比 (Gold / Copper Ratio)
        ratio = gold / copper
        current_ratio = ratio.iloc[-1]
        prev_ratio = ratio.iloc[-20]
        
        # 簡易判斷趨勢
        if current_ratio < prev_ratio:
            ratio_trend = "↘️下降 (資金流向工業)"
        else:
            ratio_trend = "↗️上升 (資金流向避險)"
        
        msg = f"⚖️ **金銅比**: `{current_ratio:.1f}` | 趨勢: {ratio_trend}\n"
        msg += f"🔗 **相關性**: `{correlation:.2f}`"
        
        if correlation < 0.3:
            msg += " (⚠️脫鉤中，留意輪動)"
        else:
            msg += " (同步波動)"
        
        msg += "\n"
        return msg
    except Exception as e:
        return f"無法計算輪動數據: {e}\n"

def get_strategy_guide():
    """
    產生策略教學 (Cheat Sheet)
    讓使用者在 Discord 中直接看到如何解讀數據
    """
    guide = """
>>> **🧠 策略戰術板 (Cheat Sheet)**
**1. 金銅比 (Gold/Copper Ratio) 怎麼看？**
• 📉 **下降趨勢**: 銅強於金。代表景氣復甦或通膨預期，資金從避險(黃金)轉向製造(銅)，有利**資源股/工業金屬**。
• 📈 **上升趨勢**: 金強於銅。代表市場恐慌或經濟衰退，資金躲回**黃金**。

**2. 什麼是「脫鉤」與「輪動」？**
• 當 **相關性 < 0.3** 且 **銅漲金跌** 時，驗證貼文說的「外溢效應」，是切入銅相關標的的好時機。

**3. RSI 技術指標操作**
• 🔥 **> 75 (過熱)**: 短線漲太多，容易回檔，**不要追高**，考慮分批停利。
• ✨ **< 30 (超賣)**: 短線跌深，乖離過大，**尋找反彈買點**。
"""
    return guide

def plot_chart(df):
    plt.figure(figsize=(10, 6))
    plt.style.use('bmh')
    
    norm_df = (df / df.iloc[0]) * 100
    
    plt.plot(norm_df.index, norm_df['GC=F'], label='Gold', color='#FFD700', linewidth=2)
    plt.plot(norm_df.index, norm_df['HG=F'], label='Copper', color='#C15436', linewidth=2.5)
    
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
        
        # 1. 宏觀分析
        msg += "### 🌏 宏觀視野\n"
        msg += analyze_rotation_logic(df)
        dxy = analyze_single_target(df, 'DX-Y.NYB')
        if dxy:
            msg += f"💵 **美元指數**: `{dxy['price']:.2f}` ({dxy['change']:+.2f}%) | {dxy['note']}\n"
        
        # 2. 貴金屬區塊
        msg += "\n### 🥇 貴金屬 (避險)\n"
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
        
        # 4. 加入策略教學 (Cheat Sheet) - 這裡呼叫新函式
        msg += get_strategy_guide()

        # 產生圖表並發送
        img_path = plot_chart(df)
        send_discord_notify(msg, img_path)

    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
