import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd
import requests
import yfinance as yf
from deep_translator import GoogleTranslator

WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
CACHE_FILE = Path(os.getenv('TRANSLATION_CACHE_FILE', str(Path(__file__).with_name('nikkei_translation_cache.json'))))
CONSTITUENTS_CACHE_FILE = Path(os.getenv('NIKKEI_CONSTITUENTS_CACHE_FILE', str(Path(__file__).with_name('nikkei225_constituents_cache.json'))))
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; Nikkei225Tracker/1.0)'}
SECTOR_MAP = {
    'Technology': '科技', 'Financials': '金融', 'Financial Services': '金融服務',
    'Industrials': '工業', 'Healthcare': '醫療保健', 'Basic Materials': '基礎原物料',
    'Materials': '原物料', 'Consumer Cyclical': '非必需消費品',
    'Consumer Defensive': '民生必需消費品', 'Consumer Goods': '消費品',
    'Communication Services': '通訊服務', 'Energy': '能源',
    'Utilities': '公用事業', 'Real Estate': '房地產',
    'Capital Goods/Others': '資本財與其他',
    'Transportation and Utilities': '運輸與公用事業',
    'Unknown': '未知產業',
}
FALLBACK_TICKERS = [
    '9983.T', '8035.T', '6857.T', '9984.T', '9433.T', '4063.T', '6367.T', '6954.T',
    '4543.T', '7267.T', '7203.T', '6758.T', '7974.T', '6098.T', '8001.T', '6501.T',
    '8306.T', '8031.T', '6861.T', '6902.T', '6273.T', '4502.T', '4568.T', '7741.T',
    '4661.T', '6981.T', '6594.T', '3382.T', '6752.T', '7201.T',
]


def load_cache():
    try:
        if CACHE_FILE.is_file():
            with CACHE_FILE.open('r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        print(f'翻譯快取讀取失敗，將重新建立：{exc}')
    return {}


def save_cache(cache):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = CACHE_FILE.with_name(CACHE_FILE.name + '.tmp')
        with temp_file.open('w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, CACHE_FILE)
    except OSError as exc:
        print(f'翻譯快取儲存失敗：{exc}')


def translate_to_chinese(text, cache):
    """只快取成功翻譯的內容；原文改變時重新翻譯。"""
    if not text or not text.strip():
        return '暫無公司簡介'
    text = text.strip()
    key = hashlib.sha256(('en>zh-TW:' + text).encode('utf-8')).hexdigest()
    cached = cache.get(key)
    if isinstance(cached, str) and cached.strip():
        print('  使用既有中文翻譯快取')
        return cached
    for attempt in range(1, 4):
        try:
            result = GoogleTranslator(source='en', target='zh-TW').translate(text)
            if result and result.strip() and result.strip() != text:
                result = result.strip()
                cache[key] = result
                save_cache(cache)
                return result
            raise ValueError('翻譯結果為空或與英文原文相同')
        except Exception as exc:
            print(f'  翻譯第 {attempt}/3 次失敗：{exc}')
            if attempt < 3:
                time.sleep(3 * attempt)
    return text + '（翻譯服務暫時無法使用）'


def configure_chinese_font():
    """優先使用使用者指定字型，再嘗試常見中日文字型。"""
    specified = os.getenv('CHINESE_FONT_PATH', '').strip()
    if specified:
        try:
            font_manager.fontManager.addfont(specified)
            name = font_manager.FontProperties(fname=specified).get_name()
            plt.rcParams['font.sans-serif'] = [name, 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
            print(f'圖表中文字型：{name}')
            return True
        except Exception as exc:
            print(f'指定中文字型無法載入：{exc}')
    candidates = [
        'Noto Sans CJK TC', 'Noto Sans TC', 'Microsoft JhengHei',
        'PingFang TC', 'Heiti TC', 'Source Han Sans TW',
        'WenQuanYi Zen Hei', 'Arial Unicode MS', 'Noto Sans CJK JP',
    ]
    installed = {font.name for font in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in installed:
            plt.rcParams['font.sans-serif'] = [name, 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
            print(f'圖表中文字型：{name}')
            return True
    print('警告：未找到中文字型，圖表將使用英文標題以避免缺字。')
    return False


def normalize_code(value):
    """將 7203、7203.0、7203.T 等形式統一成 7203.T。"""
    if pd.isna(value):
        return None
    raw = str(value).strip().upper()
    match = re.fullmatch(r'(\d{4})(?:\.0+)?(?:\.T)?', raw)
    return f'{match.group(1)}.T' if match else None


def flatten_column(column):
    """支援 Wikipedia 可能出現的多層表頭。"""
    if isinstance(column, tuple):
        pieces = [str(v).strip() for v in column if str(v).strip().lower() not in ('nan', 'unnamed')
                  and not str(v).lower().startswith('unnamed:')]
        return ' '.join(pieces).lower()
    return str(column).strip().lower()


def find_column(columns, names):
    for name in names:
        for col in columns:
            normalized = flatten_column(col)
            if normalized == name or normalized.endswith(' ' + name):
                return col
    return None


def parse_constituents_table(df):
    """解析單張表格；不將年份、註解或其他數字誤認為股票代碼。"""
    code_col = find_column(df.columns, ('code', 'ticker', 'ticker symbol', 'stock code', 'security code', 'symbol'))
    name_col = find_column(df.columns, ('company', 'company name', 'name', 'security', 'constituent'))
    sector_col = find_column(df.columns, ('sector', 'industry', 'gics sector'))
    if code_col is None or name_col is None:
        return {}
    result = {}
    for _, row in df.iterrows():
        ticker = normalize_code(row[code_col])
        name = str(row[name_col]).strip()
        if not ticker or not name or name.lower() in ('nan', 'none', '-'):
            continue
        sector = str(row[sector_col]).strip() if sector_col is not None else 'Unknown'
        if not sector or sector.lower() == 'nan':
            sector = 'Unknown'
        result[ticker] = {'Security': name, 'GICS Sector': sector}
    return result


def read_constituents_cache():
    try:
        with CONSTITUENTS_CACHE_FILE.open('r', encoding='utf-8') as file:
            cached = json.load(file)
        if isinstance(cached, dict):
            valid = {symbol: entry for symbol, entry in cached.items()
                     if normalize_code(symbol) == symbol and isinstance(entry, dict)
                     and isinstance(entry.get('Security'), str)}
            if 200 <= len(valid) <= 230:
                print(f'使用上次成功保存的日經成分股名單（{len(valid)} 檔；可能不是最新名單）')
                return valid
    except (OSError, ValueError, TypeError) as exc:
        print(f'成分股快取不可用：{exc}')
    return {}


def save_constituents_cache(constituents):
    try:
        CONSTITUENTS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONSTITUENTS_CACHE_FILE.with_name(CONSTITUENTS_CACHE_FILE.name + '.tmp')
        with temporary.open('w', encoding='utf-8') as file:
            json.dump(constituents, file, ensure_ascii=False, indent=2)
        os.replace(temporary, CONSTITUENTS_CACHE_FILE)
    except OSError as exc:
        print(f'成分股快取儲存失敗：{exc}')


def get_nikkei225_tickers_info():
    """取得 Wikipedia 日經 225 名單，驗證數量並備援到上次成功的快取。"""
    print('正在獲取日經 225 成分股名單...')
    url = 'https://en.wikipedia.org/wiki/Nikkei_225'
    try:
        response = requests.get(url, headers=HEADERS, timeout=(10, 30))
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))
        candidates = [parse_constituents_table(df) for df in tables]
        candidates = [candidate for candidate in candidates if candidate]
        # 成分股應接近 225 檔。避免錯抓 Wikipedia 其他歷史紀錄或比較表。
        valid = [candidate for candidate in candidates if 200 <= len(candidate) <= 230]
        if valid:
            best = min(valid, key=lambda candidate: abs(len(candidate) - 225))
            print(f'Wikipedia 已取得 {len(best)} 檔成分股')
            if len(best) != 225:
                print('提醒：成分股數量不是 225，請確認 Wikipedia 當前資料。')
            save_constituents_cache(best)
            return best
        counts = [len(c) for c in candidates]
        print(f'Wikipedia 未找到可信的完整成分股表格（候選筆數：{counts}）。')
    except (requests.RequestException, ValueError, ImportError, TypeError) as exc:
        print(f'無法解析 Wikipedia 成分股資料：{exc}')
    except Exception as exc:
        print(f'Wikipedia 資料發生未預期錯誤：{exc}')
    return read_constituents_cache()


def format_pe(value):
    try:
        return f'{float(value):.2f}' if value is not None else 'N/A'
    except (TypeError, ValueError):
        return 'N/A'


def get_company_details(ticker, close_price, cache):
    company_name, sector_en = str(ticker), 'Unknown'
    try:
        info = yf.Ticker(ticker).info or {}
        company_name = info.get('shortName') or info.get('longName') or ticker
        sector_en = info.get('sector') or 'Unknown'
        pe_ratio = format_pe(info.get('trailingPE') if info.get('trailingPE') is not None else info.get('forwardPE'))
        rate = info.get('trailingAnnualDividendRate')
        if isinstance(rate, (int, float)) and close_price > 0:
            div_yield = f'{max(0.0, rate / close_price * 100):.2f}%'
        else:
            raw = info.get('dividendYield')
            if isinstance(raw, (int, float)):
                # Yahoo Finance 版本的 dividendYield 可能是比例或百分比。
                div_yield = f'{(raw * 100 if raw <= 0.3 else raw):.2f}%'
            else:
                div_yield = 'N/A'
        summary_en = (info.get('longBusinessSummary') or '').strip()
        if summary_en:
            summary_en = summary_en[:300].rsplit(' ', 1)[0] if len(summary_en) > 300 else summary_en
        summary = translate_to_chinese(summary_en, cache)
        if len(summary_en) == 300 or (info.get('longBusinessSummary') or '').strip() != summary_en:
            summary += '…'
        return summary, pe_ratio, div_yield, company_name, sector_en
    except Exception as exc:
        print(f'資料獲取失敗 ({ticker})：{exc}')
        return '無法獲取簡介', 'N/A', 'N/A', company_name, sector_en


def send_webhook(content, image_buffer=None, ticker=None):
    """處理 Discord 429 與短暫性 5xx 錯誤；避免靜默失敗。"""
    for attempt in range(3):
        try:
            if image_buffer is None:
                response = requests.post(WEBHOOK_URL, json={'content': content}, timeout=30)
            else:
                image_buffer.seek(0)
                response = requests.post(
                    WEBHOOK_URL, data={'content': content},
                    files={'file': (f'{ticker}_trend.png', image_buffer, 'image/png')},
                    timeout=45,
                )
            if response.status_code in (200, 204):
                return True
            if response.status_code == 429:
                try:
                    wait = float(response.json().get('retry_after', 3))
                except (ValueError, TypeError):
                    wait = 3
                time.sleep(min(max(wait, 1), 60))
                continue
            if 500 <= response.status_code < 600:
                time.sleep(3 * (attempt + 1))
                continue
            print(f'Discord 傳送失敗：HTTP {response.status_code} {response.text[:200]}')
            return False
        except requests.RequestException as exc:
            print(f'Discord 連線失敗 ({attempt + 1}/3)：{exc}')
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    return False


def send_to_discord(ticker, info, close_price, pct_change, image_buffer,
                    summary, pe_ratio, div_yield, sector_en):
    company_name = info.get('Security', ticker)
    sector_en = sector_en or 'Unknown'
    sector_cn = SECTOR_MAP.get(sector_en, sector_en)
    trend_emoji = '📈' if pct_change > 0 else '📉'
    trend_text = '漲幅' if pct_change > 0 else '跌幅'
    message_content = (
        f'{trend_emoji} **{ticker} - {company_name}**\n'
        f'🏢 板塊: {sector_cn} ({sector_en})\n'
        f'📊 本益比 (P/E): **{pe_ratio}** | 💰 股息率: **{div_yield}**\n'
        f'📝 簡介: {summary}\n'
        f'🔹 收盤價: ¥{close_price:.2f}\n'
        f'{trend_emoji} {trend_text}: **{pct_change * 100:.2f}%**'
    )
    # Discord content 上限為 2000 字元，預留空間給其他欄位。
    if len(message_content) > 1950:
        excess = len(message_content) - 1950
        summary = summary[:max(0, len(summary) - excess - 1)] + '…'
        message_content = (
            f'{trend_emoji} **{ticker} - {company_name}**\n'
            f'🏢 板塊: {sector_cn} ({sector_en})\n'
            f'📊 本益比 (P/E): **{pe_ratio}** | 💰 股息率: **{div_yield}**\n'
            f'📝 簡介: {summary}\n'
            f'🔹 收盤價: ¥{close_price:.2f}\n'
            f'{trend_emoji} {trend_text}: **{pct_change * 100:.2f}%**'
        )
    return send_webhook(message_content[:2000], image_buffer, ticker)


def extract_close(data, ticker=None):
    """相容 yfinance 單檔與多檔下載的 MultiIndex 欄位。"""
    if data is None or data.empty:
        return pd.Series(dtype=float)
    if isinstance(data.columns, pd.MultiIndex):
        if 'Close' in data.columns.get_level_values(0):
            close = data['Close']
        elif 'Close' in data.columns.get_level_values(1):
            close = data.xs('Close', axis=1, level=1)
        else:
            return pd.Series(dtype=float)
        if isinstance(close, pd.DataFrame):
            if ticker in close.columns:
                close = close[ticker]
            elif close.shape[1] == 1:
                close = close.iloc[:, 0]
        return close
    return data['Close'] if 'Close' in data.columns else pd.Series(dtype=float)


def process_and_send_list(stock_series, title_msg, nikkei_info, line_color, cache, has_chinese_font):
    if stock_series.empty:
        print(f'{title_msg}：無符合資料')
        return
    print(f'\n--- {title_msg} ---')
    send_webhook(f'📊 **{title_msg}** 📊')
    time.sleep(1)
    for rank, (ticker, pct) in enumerate(stock_series.items(), start=1):
        print(f'處理第 {rank} 名：{ticker}')
        try:
            stock_data = yf.download(ticker, period='1y', progress=False, auto_adjust=True, threads=False)
            close = extract_close(stock_data, ticker)
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = pd.to_numeric(close, errors='coerce').dropna()
            if close.empty:
                print(f'{ticker} 無股價資料，略過')
                continue
            close_price = float(close.iloc[-1])
            summary, pe_ratio, div_yield, company_name, sector_en = get_company_details(ticker, close_price, cache)
            fig, ax = plt.subplots(figsize=(10, 5))
            try:
                ax.plot(close.index, close.values, color=line_color, linewidth=1.5)
                title_suffix = '近一年股價走勢' if has_chinese_font else '1 Year Trend'
                ax.set_title(f'{ticker} {company_name} - {title_suffix}', fontsize=14)
                ax.grid(True, linestyle='--', alpha=0.5)
                fig.tight_layout()
                buf = io.BytesIO()
                fig.savefig(buf, format='png', dpi=120)
            finally:
                plt.close(fig)
            company_info = dict(nikkei_info.get(ticker, {}))
            company_info['Security'] = company_name
            if send_to_discord(ticker, company_info, close_price, float(pct), buf,
                               summary, pe_ratio, div_yield, sector_en):
                print(f'  {ticker} 已送出 Discord')
            time.sleep(3)
        except Exception as exc:
            print(f'處理 {ticker} 時發生錯誤：{exc}')


def main():
    if not WEBHOOK_URL:
        print('錯誤：找不到 DISCORD_WEBHOOK_URL 環境變數！')
        return 1
    cache = load_cache()
    has_chinese_font = configure_chinese_font()
    nikkei_info = get_nikkei225_tickers_info()
    tickers = list(nikkei_info)
    if not tickers:
        print('警告：無法取得完整名單或成分股快取，啟用原本的 30 檔大型權值股備用清單；此時排行不是完整日經 225 排行')
        tickers = FALLBACK_TICKERS
        nikkei_info = {t: {'Security': t, 'GICS Sector': 'Unknown'} for t in tickers}
    print('正在下載股價資料...')
    try:
        data = yf.download(tickers, period='5d', progress=False, auto_adjust=True, threads=True)
        close = extract_close(data)
        if isinstance(close, pd.Series):
            close = close.to_frame(name=tickers[0])
        if close.empty or len(close) < 2:
            print('股價資料不足，無法計算日漲跌幅')
            return 1
        # 取每檔股票最近兩筆有效收盤價，避免休市或個別股票缺值造成遺漏。
        returns = close.apply(lambda s: pd.to_numeric(s, errors='coerce').dropna().pct_change().iloc[-1]
                              if pd.to_numeric(s, errors='coerce').notna().sum() >= 2 else float('nan'))
        returns = returns.dropna()
        gainers = returns[returns > 0].nlargest(10)
        losers = returns[returns < 0].nsmallest(10)
        process_and_send_list(gainers, '今日 日經 225 漲幅前十名個股報告',
                              nikkei_info, '#1f77b4', cache, has_chinese_font)
        process_and_send_list(losers, '今日 日經 225 跌幅最重個股報告',
                              nikkei_info, 'green', cache, has_chinese_font)
        return 0
    except Exception as exc:
        print(f'下載或分析股價時發生錯誤：{exc}')
        return 1


if __name__ == '__main__':
    sys.exit(main())

