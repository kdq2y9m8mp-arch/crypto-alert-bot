"""5-minute swing alerts, filtered by BTC and 4-hour trends. No trading."""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

PAIRS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]
HEADERS = {"User-Agent": "crypto-alert-bot/2.0", "Accept": "application/json"}

def ema(values, length):
    alpha = 2 / (length + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out

def rsi(values, length=14):
    delta = [b - a for a, b in zip(values, values[1:])]
    gains = [max(x, 0) for x in delta]
    losses = [max(-x, 0) for x in delta]
    gain, loss = sum(gains[:length]) / length, sum(losses[:length]) / length
    out = [50.0] * (length + 1)
    for g, l in zip(gains[length:], losses[length:]):
        gain = (gain * (length - 1) + g) / length
        loss = (loss * (length - 1) + l) / length
        out.append(100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return out

def candles(pair, seconds):
    # Coinbase Exchange supports 1h, not native 4h: aggregate aligned, complete 1h bars.
    source_seconds = 3600 if seconds == 14400 else seconds
    url = "https://api.exchange.coinbase.com/products/" + pair + "/candles?granularity=" + str(source_seconds)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = json.load(response)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    # Coinbase format: [time, low, high, open, close, volume]
    bars = sorted((bar for bar in raw if int(bar[0]) + source_seconds <= now),
                  key=lambda x: int(x[0]))
    if seconds == 14400:
        groups = {}
        for bar in bars:
            start = int(bar[0]) // 14400 * 14400
            groups.setdefault(start, []).append(bar)
        merged = []
        for start, group in sorted(groups.items()):
            group.sort(key=lambda x: int(x[0]))
            if (len(group) != 4 or start + 14400 > now
                    or [int(x[0]) for x in group] != [start + k * 3600 for k in range(4)]):
                continue
            merged.append([start, min(float(x[1]) for x in group),
                           max(float(x[2]) for x in group), float(group[0][3]),
                           float(group[-1][4]), sum(float(x[5]) for x in group)])
        bars = merged
    if len(bars) < 55:
        raise RuntimeError("Insufficient completed bars: " + pair)
    return bars

def trend(bars):
    prices = [float(x[4]) for x in bars]
    fast, slow = ema(prices, 20), ema(prices, 50)
    return prices[-1] > fast[-1] > slow[-1]

def atr(bars, length=14):
    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev = float(bars[i][2]), float(bars[i][1]), float(bars[i-1][4])
        true_ranges.append(max(high-low, abs(high-prev), abs(low-prev)))
    return sum(true_ranges[-length:]) / length

def evaluate(pair, btc_up):
    bars = candles(pair, 300)
    higher = candles(pair, 14400)
    closes = [float(x[4]) for x in bars]
    volumes = [float(x[5]) for x in bars]
    fast, slow = ema(closes, 20), ema(closes, 50)
    macd = [a-b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    signal_line = ema(macd, 9)
    hist = [a-b for a, b in zip(macd, signal_line)]
    momentum = rsi(closes)
    price = closes[-1]
    # Exclude latest bar to avoid look-ahead bias for the breakout.
    previous = bars[-49:-1]
    support = min(float(x[1]) for x in previous)
    resistance = max(float(x[2]) for x in previous)
    volume_avg = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / volume_avg if volume_avg else 0.0
    coin_up = trend(higher)
    bullish_cross = closes[-2] <= fast[-2] and price > fast[-1]
    bearish_cross = closes[-2] >= fast[-2] and price < fast[-1]
    volatility = atr(bars)
    # Stop below recent support but not closer than 1.5 ATR.
    stop = min(support - 0.15 * volatility, price - 1.5 * volatility)
    risk = price - stop
    target = price + 2.0 * risk
    # The user trades on Webull, whose spread can be ~2%: reject tiny setups.
    gross_reward_pct = (target / price - 1) * 100
    reward_ok = gross_reward_pct >= 4.0
    buy = (btc_up and coin_up and bullish_cross and fast[-1] > slow[-1]
           and 52 <= momentum[-1] <= 72 and hist[-1] > 0
           and volume_ratio >= 1.3 and reward_ok)
    sell = (bearish_cross and hist[-1] < 0) or (momentum[-2] >= 45 > momentum[-1])
    if not buy and not sell:
        return None
    label = "BUY WATCH" if buy else "SELL WATCH — only if actually held"
    lines = [
        f"{label} | {pair}",
        f"Coinbase indicative price: ${price:,.4f}",
        f"Closed 5m bar: {dt.datetime.fromtimestamp(int(bars[-1][0]),dt.timezone.utc):%Y-%m-%d %H:%M} UTC",
        f"BTC 4h trend: {'UP' if btc_up else 'NOT UP'}; coin 4h: {'UP' if coin_up else 'NOT UP'}",
        f"Support(4h range): ${support:,.4f}; resistance: ${resistance:,.4f}",
        f"Volume vs prior 20 bars: {volume_ratio:.2f}x; RSI: {momentum[-1]:.1f}",
    ]
    if buy:
        lines += [
            f"Illustrative entry: ${price:,.4f}",
            f"Stop candidate: ${stop:,.4f}; target candidate: ${target:,.4f}",
            f"Gross target: +{gross_reward_pct:.1f}%; gross risk: -{risk/price*100:.1f}%",
        ]
    else:
        lines += ["Momentum weakened; verify your actual Webull position and cost basis before selling.",
                  f"Support/reference: ${support:,.4f}; resistance: ${resistance:,.4f}"]
    lines += ["Not Webull executable prices. Spread/fees can erase returns. No automatic orders."]
    return label, "\n".join(lines)

def notify(title, message):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if len(topic) < 20 or not all(c.isalnum() or c in "-_" for c in topic):
        raise RuntimeError("Set NTFY_TOPIC GitHub secret (20+ random chars)")
    req = urllib.request.Request("https://ntfy.sh/" + urllib.parse.quote(topic, safe=""),
        data=message.encode("utf-8"), method="POST",
        headers={"Title": title[:60], "Content-Type": "text/plain; charset=utf-8",
                 "User-Agent": "crypto-alert-bot/2.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError("ntfy push failed")

def main():
    if os.getenv("TEST_NOTIFICATION") == "true":
        notify("Crypto Alerts Test", "GitHub Actions to ntfy is connected. Monitor v2.")
        return
    btc_up = trend(candles("BTC-USD", 14400))
    errors = []
    for pair in PAIRS:
        try:
            result = evaluate(pair, btc_up)
            if result:
                title, message = result
                notify(title, message)
                print("Sent:", pair, title)
            else:
                print("No signal:", pair)
        except Exception as error:
            errors.append(f"{pair}: {error}")
    if errors:
        raise RuntimeError("; ".join(errors))

if __name__ == "__main__":
    main()
