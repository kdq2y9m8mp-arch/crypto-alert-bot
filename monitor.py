import datetime as dt
import json
import os
import urllib.parse
import urllib.request

PAIRS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]
HEADERS = {"User-Agent": "crypto-alert-bot/1.0", "Accept": "application/json"}

def ema(values, length):
    alpha = 2 / (length + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result

def rsi(values, length=14):
    changes = [b - a for a, b in zip(values, values[1:])]
    gains = [max(v, 0) for v in changes]
    losses = [max(-v, 0) for v in changes]
    gain = sum(gains[:length]) / length
    loss = sum(losses[:length]) / length
    values_out = [50.0] * (length + 1)
    for g, l in zip(gains[length:], losses[length:]):
        gain = (gain * (length - 1) + g) / length
        loss = (loss * (length - 1) + l) / length
        values_out.append(100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return values_out

def get_candles(pair, granularity=300):
    request = urllib.request.Request(
        "https://api.exchange.coinbase.com/products/" + pair + f"/candles?granularity={granularity}",
        headers=HEADERS,
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = json.load(response)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    bars = sorted((v for v in raw if int(v[0]) + granularity <= now), key=lambda v: int(v[0]))
    if len(bars) < 65:
        raise RuntimeError("Not enough candles for " + pair)
    return bars

def signal_for(pair):
    bars = get_candles(pair)
    close = [float(bar[4]) for bar in bars]
    fast, slow = ema(close, 20), ema(close, 50)
    macd_fast, macd_slow = ema(close, 12), ema(close, 26)
    macd = [a - b for a, b in zip(macd_fast, macd_slow)]
    macd_signal = ema(macd, 9)
    histogram = [a - b for a, b in zip(macd, macd_signal)]
    strength = rsi(close)
    buy = (close[-2] <= fast[-2] and close[-1] > fast[-1]
           and fast[-1] > slow[-1] and strength[-1] >= 52 and histogram[-1] > 0)
    sell = ((close[-2] >= fast[-2] and close[-1] < fast[-1] and histogram[-1] < 0)
            or (strength[-2] >= 45 and strength[-1] < 45))
    if not buy and not sell:
        return None
    kind = "BUY WATCH" if buy else "SELL WATCH (only if held)"
    hourly = get_candles(pair, 3600)
    hourly_closes = [float(x[4]) for x in hourly]
    hourly_fast = ema(hourly_closes, 20)[-1]
    hourly_slow = ema(hourly_closes, 50)[-1]
    trend = "UP" if hourly_fast > hourly_slow else "DOWN/FLAT"
    aligned = (buy and hourly_fast > hourly_slow) or (sell and hourly_fast < hourly_slow)
    reason = ("5m EMA20 crossover, EMA20 > EMA50, RSI >= 52, MACD positive"
              if buy else "5m EMA20 breakdown / RSI below 45")
    low = min(float(x[1]) for x in bars[-12:])
    high = max(float(x[2]) for x in bars[-12:])
    # Historical analogues: descriptive overlapping 30-minute observations, not backtest performance.
    analogues = []
    for k in range(55, len(close) - 6):
        if fast[k] > slow[k] and histogram[k] > 0 and abs(strength[k] - strength[-1]) <= 5:
            analogues.append(100 * (close[k + 6] / close[k] - 1))
    history = ("Insufficient comparable recent samples"
               if len(analogues) < 10 else
               f"{len(analogues)} overlapping examples, {sum(x > 0 for x in analogues)} rose after 30m, "
               f"average {sum(analogues) / len(analogues):+.2f}% (not predictive)")
    return {"pair": pair, "kind": kind, "price": close[-1], "bar": int(bars[-1][0]),
            "reason": reason, "rsi": strength[-1], "macd": histogram[-1],
            "ema20": fast[-1], "ema50": slow[-1], "hourly": trend,
            "aligned": aligned, "low": low, "high": high, "history": history}

def send_notification(title, message):
    topic = os.environ.get("NTFY_TOPIC", "")
    if len(topic) < 20 or not all(c.isalnum() or c in "-_" for c in topic):
        raise RuntimeError("Set a private random NTFY_TOPIC secret of 20+ characters")
    url = "https://ntfy.sh/" + urllib.parse.quote(topic, safe="")
    request = urllib.request.Request(
        url, data=message.encode("utf-8"), method="POST",
        headers={"Title": title, "Content-Type": "text/plain; charset=utf-8", "User-Agent": "crypto-alert-bot/1.0"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError("Notification failed")

SOL_QTY = 0.76007592
SOL_ENTRY = 105.25
MAX_LOSS = 2.0

def sol_position_alert():
    bars = get_candles("SOL-USD")
    previous, current = float(bars[-2][4]), float(bars[-1][4])
    req = urllib.request.Request("https://api.exchange.coinbase.com/products/SOL-USD/ticker", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as response:
        quote = json.load(response)
    bid, ask = float(quote["bid"]), float(quote["ask"])
    if bid <= 0 or ask < bid:
        raise ValueError("Invalid SOL ticker quote")
    # Bid is Coinbase indicative; Webull executable price may differ.
    cost = SOL_QTY * SOL_ENTRY
    pnl = SOL_QTY * bid - cost
    loss_bid = (cost - MAX_LOSS) / SOL_QTY
    # Compare completed candles adjusted by latest spread estimate, to avoid repeated notices.
    offset = max(0.0, current - bid)
    previous_bid_estimate = previous - offset
    events = []
    def add(kind, detail):
        msg = (f"SOL {kind}\n"
               f"Coinbase bid: ${bid:.2f}, ask: ${ask:.2f}\n"
               f"Entry: ${SOL_ENTRY:.2f}; held: {SOL_QTY:.8f} SOL\n"
               f"Indicative value: ${bid * SOL_QTY:.2f}; P/L: ${pnl:+.2f}\n"
               f"{detail}\n"
               "Verify the Webull sell quote and fees; actual P/L can differ. No automatic sales.")
        events.append((kind, msg))
    if previous_bid_estimate > loss_bid >= bid:
        add("LOSS LIMIT WATCH", f"Indicative $2 loss threshold reached at bid ${loss_bid:.2f}. Review exit now.")
    for target in (107.90, 110.50):
        if previous_bid_estimate < target <= bid:
            add("PROFIT WATCH", f"Bid crossed ${target:.2f}. Consider profit-taking after costs.")
    hours = get_candles("SOL-USD", 3600)
    hc = [float(c[4]) for c in hours]
    ef, es = ema(hc, 20), ema(hc, 50)
    if ef[-2] >= es[-2] and ef[-1] < es[-1]:
        add("1H TREND REVERSAL", "1h EMA20 crossed below EMA50 on a closed candle. Reassess holding.")
    return events

def main():
    if os.environ.get("TEST_NOTIFICATION") == "true":
        send_notification("Crypto Alerts Test", "GitHub Actions to ntfy is connected.")
        return
    errors = []
    summary = []
    hourly = os.environ.get("HOURLY_SUMMARY") == "true"
    for pair in PAIRS:
        try:
            item = signal_for(pair)
            if item:
                timestamp = dt.datetime.fromtimestamp(item["bar"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                message = (f'{item["pair"]}: {item["kind"]}\n'
                           f'Price: ${item["price"]:,.4f}\n'
                           f'5m candle (UTC): {timestamp}\n'
                           f'Reason: {item["reason"]}\n'
                           f'EMA20: {item["ema20"]:.2f} | EMA50: {item["ema50"]:.2f}\n'
                           f'RSI14: {item["rsi"]:.1f} | MACD histogram: {item["macd"]:+.4f}\n'
                           f'1h trend: {item["hourly"]} | Signal alignment: {item["aligned"]}\n'
                           f'Recent 1h range: ${item["low"]:.2f} - ${item["high"]:.2f}\n'
                           f'Historical context: {item["history"]}\n'
                           'Outlook: alignment supports the move; disagreement raises false-signal risk.\n'
                           'Not investment advice or a guarantee. Check fees, spread and risk. No automatic trades.')
                send_notification("Crypto " + item["kind"], message)
                print("Sent", item["kind"], item["pair"])
                summary.append(f'{pair}: {item["kind"]} (${item["price"]:,.4f})')
            else:
                print("No signal:", pair)
                summary.append(f"{pair}: No signal")
        except Exception as exc:
            errors.append(pair + ": " + str(exc))
            summary.append(pair + ": Check failed")
    if hourly:
        send_notification("Crypto hourly status", "\n".join(summary) + "\nInformational only. No automatic trades.")
        print("Sent hourly status")
    try:
        for title, message in sol_position_alert():
            send_notification(title, message)
            print("Sent SOL position alert:", title)
    except Exception as exc:
        errors.append("SOL position: " + str(exc))
    if errors:
        raise RuntimeError("; ".join(errors))

if __name__ == "__main__":
    main()
