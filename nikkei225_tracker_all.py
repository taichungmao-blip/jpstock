import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import requests
import io
import os
import sys
import time
from deep_translator import GoogleTranslator

# ================= 設定區 =================
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# 台股 GICS 板塊翻譯對應 (可依需求擴充)
SECTOR_MAP = {
    'Technology': '科技',
    'Financials': '金融',
    'Consumer Goods': '消費品',
    'Materials': '原物料',
    'Capital Goods/Others': '資本財與其他',
    'Transportation and Utilities': '運輸與公用事業',
    'Semiconductors': '半導體',
    'Real Estate': '不動產',
    'Healthcare': '醫療保健'
}

if not WEBHOOK_URL:
    print("錯誤：找不到 DISCORD_WEBHOOK_URL 環境變數！")
    sys.exit(1)
# ==========================================

def get_tw_top300_tickers_info():
    """
    獲取台股市值前 300 大清單。
    建議：寫一支獨立的小爬蟲，定期從證交所或選股網站抓取市值前 300 大的代號，
    並存成 'tw_top300_tickers.txt'，每行一個代號（需包含 .TW 或 .TWO）。
    """
    info_dict = {}
    file_path = 'tw_top300_tickers.txt'
    
    print("正在獲取台股市值前 300 大清單...")
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    ticker = line.strip()
                    if ticker:
                        info_dict[ticker] = {'Security': ticker, 'GICS Sector': 'Unknown'}
            print(f"成功從檔案載入 {len(info_dict)} 檔股票。")
            return info_dict
        except Exception as e:
            print(f"讀取 {file_path} 失敗: {e}")
            
    print("警告：找不到清單檔案或讀取失敗，啟用台股前 50 大權值股備用清單。")
    # 備用清單：台股前 50 大權值股 (市值前段班範例)
    fallback_tickers = [
        '2330.TW', '2317.TW', '2454.TW', '2308.TW', '2382.TW', '2881.TW', '2882.TW', '2412.TW',
        '2891.TW', '3711.TW', '2886.TW', '2884.TW', '1216.TW', '2885.TW', '3231.TW', '2892.TW',
        '2002.TW', '2345.TW', '2880.TW', '2890.TW', '2357.TW', '5880.TW', '2303.TW', '2395.TW',
        '2883.TW', '2887.TW', '2379.TW', '3045.TW', '4938.TW', '3034.TW', '2327.TW', '2603.TW',
        '3008.TW', '2207.TW', '5871.TW', '6669.TW', '2301.TW', '2356.TW', '2912.TW', '3661.TW',
        '2888.TW', '2615.TW', '1101.TW', '2408.TW', '1590.TW', '9910.TW', '1102.TW', '2609.TW',
        '2371.TW', '8454.TW'
    ]
    
    for t in fallback_tickers:
        info_dict[t] = {'Security': t, 'GICS Sector': 'Unknown'}
        
    return info_dict

def get_company_details(ticker, close_price):
    """獲取簡介、精準股息率與公司名稱"""
    try:
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        
        # 取得公司名稱 (台股通常 longName 會是中文全稱，shortName 不一定有)
        company_name = info.get('longName', info.get('shortName', ticker))
        if not company_name or company_name == ticker:
            company_name = ticker
            
        pe_ratio = info.get('trailingPE', info.get('forwardPE', 'N/A'))
        if isinstance(pe_ratio, (int, float)):
            pe_ratio = f"{pe_ratio:.2f}"
            
        trailing_div_rate = info.get('trailingAnnualDividendRate')
        if isinstance(trailing_div_rate, (int, float)) and close_price > 0:
            div_yield = (trailing_div_rate / close_price) * 100
            div_yield_str = f"{div_yield:.2f}%" if div_yield > 0 else "0.00%"
        else:
            raw_yield = info.get('dividendYield')
            if isinstance(raw_yield, (int, float)):
                div_yield_str = f"{raw_yield:.2f}%" if raw_yield > 0.3 else f"{raw_yield * 100:.2f}%"
            else:
                div_yield_str = "N/A"

        summary_en = info.get('longBusinessSummary', '')
        if not summary_en:
            return "暫無簡介", pe_ratio, div_yield_str, company_name
        if len(summary_en) > 300:
            summary_en = summary_en[:300]

        # Yahoo Finance 台股的 summary 通常是英文，這裡保留你的翻譯邏輯
        translator = GoogleTranslator(source='auto', target='zh-TW')
        summary_zh = translator.translate(summary_en) + "..."
        
        return summary_zh, pe_ratio, div_yield_str, company_name
    except Exception as e:
        print(f"資料獲取或翻譯失敗 ({ticker}): {e}")
        return "無法獲取簡介", "N/A", "N/A", ticker

def send_to_discord(ticker, info, close_price, pct_change, image_buffer, summary, pe_ratio, div_yield):
    company_name = info.get('Security', ticker)
    sector_en = info.get('GICS Sector', 'Unknown')
    sector_cn = SECTOR_MAP.get(sector_en, sector_en)
    
    trend_emoji = "📈" if pct_change > 0 else "📉"
    trend_text = "漲幅" if pct_change > 0 else "跌幅"
    
    message_content = (
        f"{trend_emoji} **{ticker} - {company_name}**\n"
        f"🏢 版塊: {sector_cn} ({sector_en})\n"
        f"📊 本益比 (P/E): **{pe_ratio}** |  💰 股息率: **{div_yield}**\n"
        f"📝 簡介: {summary}\n"
        f"🔹 收盤價: NT${close_price:.2f}\n"
        f"{trend_emoji} {trend_text}: **{pct_change * 100:.2f}%**" 
    )
    
    payload = {"content": message_content}
    image_buffer.seek(0)
    files = {"file": (f"{ticker}_1Y.png", image_buffer, "image/png")}
    requests.post(WEBHOOK_URL, data=payload, files=files)

def process_and_send_list(stock_series, title_msg, tw_info, line_color):
    if stock_series.empty:
        print(f"{title_msg} 無符合資料")
        return
        
    print(f"\n--- {title_msg} ---")
    requests.post(WEBHOOK_URL, json={"content": f"📊 **{title_msg}** 📊"})
    time.sleep(1)
    
    for rank, (ticker, pct) in enumerate(stock_series.items(), start=1):
        try:
            stock_data = yf.download(ticker, period="9mo", progress=False)
            if stock_data.empty: continue
            
            close_price = stock_data['Close'].iloc[-1].item()
            
            summary, pe_ratio, div_yield, company_name = get_company_details(ticker, close_price)
            
            plt.figure(figsize=(10, 5))
            plt.plot(stock_data.index, stock_data['Close'], color=line_color, linewidth=1.5)
            plt.title(f"{ticker} {company_name} - 1 Year Trend", fontsize=14)
            plt.grid(True, linestyle='--', alpha=0.5)
            plt.tight_layout()
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            plt.close()
            
            company_info = tw_info.get(ticker, {})
            company_info['Security'] = company_name 
            
            send_to_discord(ticker, company_info, close_price, pct, buf, summary, pe_ratio, div_yield)
            time.sleep(2) 
        except Exception as e:
            print(f"處理 {ticker} 時發生錯誤: {e}")

def main():
    tw_info = get_tw_top300_tickers_info()
    tickers = list(tw_info.keys())
    
    print(f"正在下載 {len(tickers)} 檔股價資料...")
    # 一次下載多檔可能會遇到 Yahoo Finance 暫時限流，progress=False 保持畫面乾淨
    data = yf.download(tickers, period="5d", progress=False)['Close']
    
    if data.empty:
        print("無法獲取任何股價資料，程式結束。")
        return

    # 計算單日漲跌幅 (與前一個交易日相比)
    returns = data.pct_change().iloc[-1].dropna()
    
    gainers = returns[returns > 0]
    losers = returns[returns < 0]
    
    top_10_gainers = gainers.nlargest(10)
    top_10_losers = losers.nsmallest(10)
    
    # 台灣習慣：紅色代表上漲，綠色代表下跌
    process_and_send_list(top_10_gainers, "今日 台股市值前 300 大漲幅前十名個股報告", tw_info, 'red')
    process_and_send_list(top_10_losers, "今日 台股市值前 300 大跌幅最重個股報告", tw_info, 'green')

if __name__ == "__main__":
    main()
