SIGNAL — LIVE TEST VERSION (BTC + ETH → Telegram)

GOAL
Continuously check 1m BTC/USDT + ETH/USDT quotes. After two confirmed
local minima 30-45 min apart, calculate the yellow line exactly halfway
between the two low values. Once a newly formed pair is being tracked,
notify via Telegram as price approaches (warning) and first reaches the
yellow line (entry candidate). Continue monitoring. No orders, no exchange
API keys, no 100% buy claims. This is a prototype interpretation of the
screenshots, NOT confirmed profitable strategy.

UNKNOWN CONDITIONS THAT THE CLIENT MUST VERIFY
- How to distinguish the two significant "jumps" from ordinary 1m dips.
  Current technical assumption: local low confirmed with 2 subsequent
  CLOSED 1m bars (PIVOT_RADIUS=2). No rebound filters.
- Exactly where the purple warning line is. Current temporary setting
  WARNING_BUFFER_PCT=0.15 (%) above yellow midpoint.
- How to calculate the blue strong-buy level; NOT implemented rather than
  fabricating it.
- Confirmation that third crossing of yellow midpoint is an entry.

LIVE BEHAVIOR
- Fetch public Binance 1m candles for BTC and ETH; Kraken USD fallback on
  public-data fetch errors (exchange and currency shown explicitly).
- Poll every 20 seconds. Last incomplete candle used ONLY for live price,
  pivot lows are evaluated with CLOSED candles.
- No historical alert flood at launch: existing formations are treated as
  baseline. New price moves after observation may trigger Telegram messages.
- One newest eligible pair of minima is monitored per asset, to avoid
  duplicate warnings for dozens of overlapping pairs. This is a technical
  candidate selection policy, not a client requirement.
- Two alert types, one time each per selected formation: WARNING, ENTRY.
- No web server, domain, or phone-on requirement after Railway deployment.

DEPLOY FROM iPHONE: GitHub → Railway
1. Upload pattern_bot.py and Dockerfile to root of existing PRIVATE
   GitHub repo signal-trading-bot (replace OLD versions). Optional tests.
2. Railway: New Project → GitHub repository → signal-trading-bot.
   Railway will detect Dockerfile. If outage persists, use another host.
3. Railway service → Variables:
   TELEGRAM_BOT_TOKEN = a NEW regenerated token from BotFather
   TELEGRAM_CHAT_ID = chat ID found earlier
   SYMBOLS = BTC,ETH [optional]
   POLL_SECONDS = 20 [optional, minimum 10]
   WARNING_BUFFER_PCT = 0.15 [optional, UNCONFIRMED]
4. Deploy. Logs should show 'Signal запущен', then candle summaries.
5. Telegram receives alerts only when a NEW crossing event happens.
   A blank chat is normal when no crossings occur.
6. To verify delivery independently of market conditions, run
   python pattern_bot.py --telegram-test on a machine with these variables.
   (No need to wait for a real entry event.)

QUICK CHECK IN GOOGLE COLAB
- Run the accompanying Signal_live_test_Colab.ipynb notebook.
- It simulates warning and entry OFFLINE, then checks Binance/Kraken access.
- DO NOT paste the Telegram token into notebook code or screenshots.

IMPORTANT SECURITY
The bot token used in the conversation was made public. Revoke/regenerate
with @BotFather before deploying to a server. Don't publish tokens to GitHub.

RISKS
- Signals are NOT buy orders, predictions, or guarantees of profit.
- Dashboard/server uptime and data-provider availability are external.
- Polling and 1m confirmation entail latency; not tick-accurate execution.
- New deployment starts fresh; it does not send an alert for earlier crosses.
- Original customer rules remain partly ambiguous; get confirmation on the
  yellow/purple/blue level interpretation before paid production use.
