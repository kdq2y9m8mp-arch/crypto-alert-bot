"""Hourly market digest emailed through a user-configured SMTP account."""
import datetime as dt
import html
import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from zoneinfo import ZoneInfo

PAIRS = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD")
HEADERS = {"User-Agent": "crypto-alert-bot/1.0", "Accept": "application/json"}
TZ = ZoneInfo("America/Los_Angeles")

def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as res:
        return json.load(res)

def get_market(pair):
    ticker = fetch_json("https://api.exchange.coinbase.com/products/" + pair + "/ticker")
    price = float(ticker["price"])
    raw = fetch_json("https://api.exchange.coinbase.com/products/" + pair + "/candles?granularity=3600")
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    bars = sorted((bar for bar in raw if int(bar[0]) + 3600 <= now), key=lambda x: int(x[0]))
    if len(bars) < 25:
        return price, None, None
    # Compare completed hourly candle closes (not the live ticker) for consistent intervals.
    current, prev_hour, prev_day = [float(bars[i][4]) for i in (-1, -2, -25)]
    return price, (current / prev_hour - 1) * 100, (current / prev_day - 1) * 100

def pct(v):
    return "N/A" if v is None else f"{v:+.2f}%"

def main():
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").replace(" ", "")
    recipient = os.getenv("REPORT_TO", "").strip()
    if not user or not password or not recipient:
        raise RuntimeError("Configure SMTP_USER, SMTP_APP_PASSWORD and REPORT_TO GitHub Actions secrets first")
    now = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M PT")
    rows = []
    for pair in PAIRS:
        try:
            price, hour, day = get_market(pair)
            rows.append((pair.split("-")[0], f"${price:,.4f}", pct(hour), pct(day)))
        except Exception as exc:
            print(f"Data unavailable for {pair}: {type(exc).__name__}")
            rows.append((pair.split("-")[0], "N/A", "N/A", "N/A"))
    lines = ["Hourly crypto market report", f"Checked: {now}", "",
             "Coin | USD spot price | 1h* | 24h*"]
    lines += [" | ".join(row) for row in rows]
    lines += ["", "*Hourly completed candle closes; approximate returns, not live spot-to-spot changes.",
              "Source: Coinbase Exchange API (ticker, hourly candles)",
              "https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductticker",
              "https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles",
              "Prices are volatile. This is informational, not a trading signal. No trades executed."]
    plain = "\n".join(lines)
    trs = "".join("<tr>" + "".join(f"<td style='padding:8px;border-bottom:1px solid #ddd'>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    body = (f"<h2>仮想通貨・毎時市場レポート</h2><p>取得時刻: {html.escape(now)}</p>"
            "<table style='border-collapse:collapse'><tr><th>銘柄</th><th>USD価格</th><th>1時間*</th><th>24時間*</th></tr>"
            + trs + "</table>"
            "<p>*確定済み1時間足終値の比較による概算です。価格は取得時点のスポット価格です。</p>"
            "<p>出典: <a href='https://api.exchange.coinbase.com/'>Coinbase Exchange API</a>（ticker、hourly candles）</p>"
            "<p>値動きに注意してください。売買シグナル・自動売買ではありません。</p>")
    msg = EmailMessage()
    msg["Subject"] = f"仮想通貨・市場レポート｜{now}"
    msg["From"] = user
    msg["To"] = recipient
    msg.set_content(plain)
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as client:
        client.login(user, password)
        client.send_message(msg)
    print("Hourly email submitted successfully")

if __name__ == "__main__":
    main()
