import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime
import math
from scipy.stats import norm
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import warnings

warnings.filterwarnings("ignore")

# === Black-Scholes Greeks: Delta & Theta === #
def black_scholes_greeks(S, K, T, r, sigma):
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    delta = norm.cdf(d1)
    theta = (- (S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365
    return delta, theta

# === Upload to Google Sheets === #
def upload_to_google_sheets(dataframe, sheet_name="Options_Investment"):
    scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open(sheet_name).sheet1
    sheet.clear()
    sheet.update([dataframe.columns.values.tolist()] + dataframe.values.tolist())

# === Get S&P 500 List === #
def get_sp500_tickers():
    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
    df = pd.read_csv(url)
    return df['Symbol'].tolist()

def fix_ticker_format(ticker):
    return ticker.replace('.', '-')

def get_top_volume_tickers(limit=100):
    tickers = get_sp500_tickers()
    tickers = [fix_ticker_format(tk) for tk in tickers]

    volume_data = {}
    for ticker in tickers:
        try:
            data = yf.Ticker(ticker).history(period="1d")
            if not data.empty:
                volume_data[ticker] = data['Volume'].iloc[-1]
        except:
            continue

    # 排序成 DataFrame 再篩選前N名
    volume_df = pd.DataFrame(list(volume_data.items()), columns=["Ticker", "Volume"])
    volume_df = volume_df.sort_values(by="Volume", ascending=False)

    return volume_df["Ticker"].head(limit).tolist()


# === Get Top 100 Volume Tickers === #
def get_top_volume_tickers(limit=100):
    tickers = get_sp500_tickers()
    volumes = []
    for ticker in tickers:
        try:
            data = yf.download(ticker, period="1d", interval="1d", progress=False)
            if not data.empty:
                volume = data['Volume'].iloc[-1]
                volumes.append((ticker, volume))
        except:
            continue
    top = sorted(volumes, key=lambda x: x[1], reverse=True)[:limit]
    return [t[0] for t in top]

# === Screener Main Function === #
def screen_stocks_with_greeks(stock_list):
    results = []
    for ticker in stock_list:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="6mo")
            if hist.empty or len(hist) < 30:
                continue

            # 技術指標
            hist['rsi'] = ta.rsi(hist['Close'], length=14)
            bb = ta.bbands(hist['Close'], length=20)
            macd = ta.macd(hist['Close'])
            hist = pd.concat([hist, bb, macd], axis=1)

            last = hist.iloc[-1]
            prev = hist.iloc[-2]
            current_price = last['Close']

            bollinger = last['Close'] > last['BBU_20_2.0']
            rsi_cross = prev['RSI_14'] < 30 and last['RSI_14'] > 30
            macd_cross = prev['MACD_12_26_9'] < prev['MACDs_12_26_9'] and last['MACD_12_26_9'] > last['MACDs_12_26_9']

            if not (bollinger or rsi_cross or macd_cross):
                continue

            beta = stock.info.get('beta', 0)
            if beta is None or beta <= 1:
                continue

            # 基本面
            fin = stock.financials
            if fin.empty or 'Total Revenue' not in fin.index:
                continue
            revenue = fin.loc['Total Revenue']
            if len(revenue) < 2:
                continue
            rev_growth = (revenue[0] - revenue[1]) / revenue[1]

            eps_df = stock.earnings
            if eps_df.empty or len(eps_df) < 2:
                continue
            eps_growth = (eps_df['Earnings'][-1] - eps_df['Earnings'][-2]) / abs(eps_df['Earnings'][-2])

            if rev_growth <= 0 and eps_growth <= 0:
                continue

            # 選擇權
            expirations = stock.options
            valid_dates = [d for d in expirations if 7 <= (datetime.strptime(d, "%Y-%m-%d") - datetime.today()).days <= 21]
            if not valid_dates:
                continue
            expiry = valid_dates[0]
            option_chain = stock.option_chain(expiry)
            calls = option_chain.calls
            calls = calls[
                (calls['strike'] <= current_price * 1.02) &
                (calls['lastPrice'] <= 2.5) &
                (calls['openInterest'] > 500)
            ]
            if calls.empty:
                continue
            calls['spread'] = (calls['ask'] - calls['bid']) / calls['lastPrice']
            calls = calls[calls['spread'] < 0.1]
            if calls.empty:
                continue

            # IV Rank 計算
            ivs = []
            for d in expirations[:6]:
                try:
                    opt = stock.option_chain(d).calls
                    atm = opt.iloc[(opt['strike'] - current_price).abs().argsort()[:1]]
                    iv = atm['impliedVolatility'].values[0]
                    ivs.append(iv)
                except:
                    continue
            if len(ivs) < 2:
                continue
            iv_rank = (ivs[-1] - min(ivs)) / (max(ivs) - min(ivs)) * 100
            if iv_rank < 40:
                continue

            # 選擇最佳 Call
            top_call = calls.sort_values(by='volume', ascending=False).iloc[0]
            strike = top_call['strike']
            premium = top_call['lastPrice']
            iv = top_call['impliedVolatility']
            T = (datetime.strptime(expiry, "%Y-%m-%d") - datetime.today()).days / 365
            r = 0.02
            delta, theta = black_scholes_greeks(S=current_price, K=strike, T=T, r=r, sigma=iv)

            if not (0.4 <= delta <= 0.7):
                continue
            if abs(theta) > 0.1 * premium:
                continue

            results.append({
                'Ticker': ticker,
                'Price': round(current_price, 2),
                'Beta': round(beta, 2),
                'IV Rank': round(iv_rank, 2),
                'EPS YoY %': round(eps_growth * 100, 2),
                'Revenue YoY %': round(rev_growth * 100, 2),
                'Strike': strike,
                'Expiry': expiry,
                'Premium': premium,
                'Delta': round(delta, 2),
                'Theta': round(theta, 4),
                'OI': top_call['openInterest'],
                'Bid': top_call['bid'],
                'Ask': top_call['ask'],
                'Spread %': round(top_call['spread'] * 100, 2),
                'MACD Cross': macd_cross,
                'RSI Rebound': rsi_cross,
                'Bollinger Breakout': bollinger
            })
        except:
            continue

    return pd.DataFrame(results)

# === Run Main === #
if __name__ == "__main__":
    tickers = get_top_volume_tickers(limit=100)
    df = screen_stocks_with_greeks(tickers)
    if not df.empty:
        df.to_csv("call_option_screening_result.csv", index=False)
        upload_to_google_sheets(df, sheet_name="Option Screener")
        print("✅ 上傳成功，共選出", len(df), "筆符合條件的 Call 合約")
    else:
        print("❌ 今日無符合條件的標的")
