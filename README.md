# Crypto Swing Alerts

Free, informational crypto watch for BTC, ETH, SOL and XRP. GitHub Actions invokes `monitor.py` approximately every five minutes, subject to GitHub scheduling delays. Notifications go to ntfy using the existing `NTFY_TOPIC` secret.

## Monitoring logic
- Closed 5-minute Coinbase Exchange candles: EMA20/50, RSI14, MACD and relative volume.
- 4-hour trend aggregated from complete hourly candles (Coinbase Exchange has no native 4h candles).
- 4-hour range *of 5-minute candles* (48 previous completed bars) as local support/resistance.
- Illustrative ATR-based stop and 2R price target; Coinbase prices are not Webull quotes or executable prices.
- Buy alerts need BTC and asset uptrends, EMA crossover, MACD, RSI, strong volume and potential target >4% gross.
- Sell-watch alerts are *not* aware of Webull positions or cost basis.

## Free public information context
`social_signals.py` requests public CoinDesk/Cointelegraph RSS, Ethereum Foundation RSS, and Google News-indexed Reddit posts (NOT direct Reddit API and NOT exhaustive Reddit monitoring). It checks up to six hours of dated headlines and matches coin names. Failed feeds remain unavailable; no made-up sentiment scores.

- When an existing technical buy/sell-watch triggers, include related fresh headlines as **unverified context**.
- When a published news/official headline is less than five minutes old and coincides with a new 5m move >=0.5% and volume >=1.5x prior 20 bars, send a separate **RESEARCH WATCH** notification, NOT a buy recommendation.
- No news-only or Reddit-only buy signals. News correlation is not proof of causation.
- Free source availability, network access, missing timestamps, rate limits and GitHub scheduling delays can all reduce coverage.
- A future X/Twitter feed would require a separately authorized source and potential API costs.

## Testing
Actions -> Crypto Swing Alerts -> Run workflow -> uncheck `test_notification` -> Run workflow. A successful run with no notification is normal when no qualifying signal occurs. View Actions logs for `RSS context:` and `No trading signal:` lines. The checked test input only tests ntfy.

## Security
Never commit the ntfy topic, account numbers, passwords or API tokens. The GitHub secret remains `NTFY_TOPIC`. This tool makes no orders. Review Webull spreads/fees and use independent protections for actual positions.
