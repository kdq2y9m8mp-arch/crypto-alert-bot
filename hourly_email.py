"""Hourly crypto digest: observed prices, reported context and conditional scenarios."""
import datetime as dt
import html
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from zoneinfo import ZoneInfo

PAIRS = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; crypto-market-report/1.1)", "Accept": "application/json"}
TZ = ZoneInfo("America/Los_Angeles")

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()

def candles(pair, granularity):
    data = json.loads(fetch("https://api.exchange.coinbase.com/products/" + pair +
                            "/candles?granularity=" + str(granularity)))
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    return sorted((b for b in data if int(b[0]) + granularity <= now), key=lambda b: int(b[0]))

def get_market(pair):
    ticker = json.loads(fetch("https://api.exchange.coinbase.com/products/" + pair + "/ticker"))
    price = float(ticker["price"])
    hourly = candles(pair, 3600)
    daily = candles(pair, 86400)
    if len(hourly) < 26 or len(daily) < 32:
        return dict(price=price, hour=None, day=None, week=None, trend="データ不足", history="比較データ不足")
    h = [float(b[4]) for b in hourly]
    d = [float(b[4]) for b in daily]
    hour = (h[-1] / h[-2] - 1) * 100
    day = (h[-1] / h[-25] - 1) * 100
    week = (d[-1] / d[-8] - 1) * 100
    if day < -2 and week < 0:
        trend = "短期・週間とも弱い。下落継続に注意"
    elif day > 2 and week > 0:
        trend = "短期・週間とも強い。ただし反落に注意"
    elif day * week < 0:
        trend = "短期と週間の方向が異なる。方向感が不安定"
    else:
        trend = "方向感は限定的。値動きの確認が必要"
    # Historical analog: past *completed* daily returns of similar size,
    # and their subsequent day's return. No look-ahead in the current day.
    daily_returns = [(d[i] / d[i-1] - 1) * 100 for i in range(1, len(d))]
    matches = [daily_returns[i+1] for i in range(len(daily_returns)-1)
               if abs(daily_returns[i] - day) <= 1.5]
    if len(matches) >= 8:
        up = sum(x > 0 for x in matches)
        avg = sum(matches) / len(matches)
        history = (f"過去約{len(d)}日で前日比が今回の24時間変化±1.5ptに近い日が"
                   f"{len(matches)}件。翌日上昇は{up}/{len(matches)}件（{up/len(matches):.0%}）、"
                   f"翌日平均{avg:+.2f}%。少数標本で予測ではありません")
    else:
        history = f"類似した下落・上昇日の例は{len(matches)}件のみ。統計的傾向は判断できません"
    return dict(price=price, hour=hour, day=day, week=week, trend=trend, history=history)

def recent_news():
    # Headlines provide context, NOT proof of price causation.
    q = urllib.parse.quote("bitcoin crypto market when:1d")
    url = "https://news.google.com/rss/search?q=" + q + "&hl=en-US&gl=US&ceid=US:en"
    try:
        root = ET.fromstring(fetch(url))
        trusted = ("Reuters", "CoinDesk", "Bloomberg", "CNBC", "MarketWatch",
                   "Barron's", "The Block", "Financial Times", "Associated Press")
        items = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if any(s.lower() in title.lower() for s in trusted) and link.startswith("https://"):
                items.append((title[:150], link))
            if len(items) >= 3:
                break
        return items
    except Exception as exc:
        print("News unavailable:", type(exc).__name__)
        return []

def pct(v):
    return "取得不可" if v is None else f"{v:+.2f}%"

def main():
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").replace(" ", "")
    recipient = os.getenv("REPORT_TO", "").strip()
    if not user or not password or not recipient:
        raise RuntimeError("Configure SMTP_USER, SMTP_APP_PASSWORD and REPORT_TO secrets")
    now = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M PT")
    rows, details = [], []
    for pair in PAIRS:
        coin = pair.split("-")[0]
        try:
            m = get_market(pair)
            rows.append((coin, f"${m['price']:,.4f}", pct(m["hour"]), pct(m["day"]), pct(m["week"])))
            details.append((coin, m["trend"], m["history"]))
        except Exception as exc:
            print("Data unavailable for", coin, type(exc).__name__)
            rows.append((coin, "取得不可", "取得不可", "取得不可", "取得不可"))
            details.append((coin, "データを取得できませんでした", "比較不可"))
    news = recent_news()
    news_text = "\n".join("- " + title + " (" + link + ")" for title, link in news) if news else "信頼できる直近の関連見出しを取得できませんでした"
    overview = ("【なぜ動いた？】価格データだけでは原因を特定できません。"
                "下記の報道は背景候補であり、値動きとの因果関係は未検証です。"
                "金利見通し・株式市場・ETF資金フロー・規制・清算などの影響も考えられますが、"
                "個別要因は裏付けがある場合のみ断定できます。")
    outlook = ("【今後24〜72時間の条件付き見通し】24時間と7日間がともに下落なら"
               "続落リスクを警戒。24時間が反発しても7日間が下落なら一時的な戻りの可能性。"
               "両方上昇なら回復基調の可能性があります。いずれも予測の確約ではありません。")
    lines = ["仮想通貨・市場レポート", "取得時刻: " + now, "",
             "銘柄 | USD価格 | 1時間* | 24時間* | 7日*"]
    lines += [" | ".join(r) for r in rows]
    lines += ["", overview, "", "【関連報道（見出しのみ）】", news_text, "", outlook, "",
              "【過去の類似ケース（過去約300日の価格履歴から集計）】"]
    lines += [f"{coin}: {trend} / {hist}" for coin, trend, hist in details]
    lines += ["", "*変動率は確定済み時間足・日足の終値比較による概算。現在値はスポット価格。",
              "出典: Coinbase Exchange API / Google News RSSの各媒体見出し（取得できた場合）",
              "https://api.exchange.coinbase.com/", "これは情報提供のみ。売買シグナルや将来価格の保証ではありません。"]
    plain = "\n".join(lines)
    th = "<tr>" + "".join("<th style='padding:8px;text-align:left;border-bottom:2px solid #ddd'>" + html.escape(x) + "</th>" for x in ("銘柄","USD価格","1時間*","24時間*","7日*")) + "</tr>"
    trs = "".join("<tr>" + "".join("<td style='padding:8px;border-bottom:1px solid #ddd'>" + html.escape(cell) + "</td>" for cell in row) + "</tr>" for row in rows)
    news_html = ("<ul>" + "".join("<li><a href='" + html.escape(link, quote=True) + "'>" + html.escape(title) + "</a></li>" for title, link in news) + "</ul>") if news else "<p>信頼できる直近の見出しを取得できませんでした。</p>"
    detail_html = "".join("<p><b>" + html.escape(coin) + "</b>: " + html.escape(trend) + "<br>" + html.escape(hist) + "</p>" for coin, trend, hist in details)
    body = ("<div style='font-family:Arial,sans-serif;line-height:1.6;max-width:720px'>"
            "<h2>仮想通貨・毎時市場レポート</h2><p>取得時刻: " + html.escape(now) + "</p>"
            "<table style='border-collapse:collapse'>" + th + trs + "</table>"
            "<h3>なぜ動いた？</h3><p>" + html.escape(overview) + "</p>"
            "<h3>直近の関連報道（原因と断定しません）</h3>" + news_html
            + "<h3>この先の傾向（条件付き）</h3><p>" + html.escape(outlook) + "</p>"
            "<h3>過去の類似ケースと銘柄別傾向</h3>" + detail_html
            + "<p><small>*変動率は確定済み時間足・日足の終値比較による概算。"
            "価格は取得時点のスポット価格。過去の傾向は将来を保証しません。<br>"
            "価格・過去データ: <a href='https://api.exchange.coinbase.com/'>Coinbase Exchange API</a>。"
            "関連報道: Google News RSSに掲載された各媒体の見出し。売買シグナルではありません。</small></p></div>")
    msg = EmailMessage()
    msg["Subject"] = "仮想通貨・市場レポート｜" + now
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
