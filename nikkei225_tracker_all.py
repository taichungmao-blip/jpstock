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
        print('警告：無法取得完整名單，啟用原本的 30 檔大型權值股備用清單')
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
