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

def get_candles(pair):
    request = urllib.request.Request(
        "https://api.exchange.coinbase.com/products/" + pair + "/candles?granularity=300",
        headers=HEADERS,
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = json.load(response)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    bars = sorted((v for v in raw if int(v[0]) + 300 <= now), key=lambda v: int(v[0]))
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
    return {"pair": pair, "kind": kind, "price": close[-1], "bar": int(bars[-1][0])}

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

def main():
    if os.environ.get("TEST_NOTIFICATION") == "true":
        send_notification("Crypto Alerts Test", "GitHub Actions to ntfy is connected.")
        return
    errors = []
    for pair in PAIRS:
        try:
            item = signal_for(pair)
            if item:
                timestamp = dt.datetime.fromtimestamp(item["bar"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                message = (f'{item["pair"]}: {item["kind"]}\n'
                           f'Price: ${item["price"]:,.4f}\n'
                           f'5-minute bar closed: {timestamp}\n'
                           'Check fees, spread, higher timeframe, and risk before trading. '
                           'Sell alert applies only if you hold the coin. No automatic trades.')
                send_notification("Crypto " + item["kind"], message)
                print("Sent", item["kind"], item["pair"])
            else:
                print("No signal:", pair)
        except Exception as exc:
            errors.append(pair + ": " + str(exc))
    if errors:
        raise RuntimeError("; ".join(errors))

if __name__ == "__main__":
    main()
