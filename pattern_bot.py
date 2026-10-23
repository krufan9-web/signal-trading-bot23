#!/usr/bin/env python3
"""Signal: live detection of a proposed three-touch pattern; Telegram alerts, NO trades.

Verified task facts:
  - first and second reference local lows 30-45 minutes apart, either may be lower;
  - yellow level = arithmetic midpoint of those two lows;
  - warn when price approaches the level, entry CANDIDATE when it reaches the level;
  - send Telegram alerts and continue monitoring.

Unspecified by the client:
  - an exact price distance for the purple warning line;
  - what exactly makes a visually marked 'jump' a valid first/second reference;
  - how to compute the lower blue 'strong buy' line.

Accordingly the warning distance and local-low confirmation are explicit *technical*
assumptions, not claims about the client's proprietary strategy. No strong-buy
100% promises or order placement are implemented.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOG = logging.getLogger("Signal")


@dataclass(frozen=True)
class Candle:
    minute: int  # Unix timestamp, UTC, in whole minutes
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Market:
    symbol: str  # BTC or ETH
    pair: str  # BTC/USDT or BTC/USD
    exchange: str
    candles: tuple[Candle, ...]  # CLOSED one-minute candles only
    price: float  # latest available intra-minute price


@dataclass(frozen=True)
class Rule:
    min_gap: int = 30
    max_gap: int = 45
    pivot_radius: int = 2  # confirm a local minimum using 2 future CLOSED candles
    # Provisional approach distance; NOT supplied numerically in original task.
    warning_buffer_pct: float = 0.15
    max_age_min: int = 240  # technical horizon of the reviewed four-hour window


@dataclass(frozen=True)
class Candidate:
    first: Candle
    second: Candle
    level: float

    @property
    def key(self) -> str:
        return f"{self.first.minute}:{self.second.minute}"


@dataclass
class Watch:
    key: str = ""
    source_key: str = ""
    armed: bool = False
    warned: bool = False
    entered: bool = False
    last_candle: int = -1


def http_json(url: str, post: Optional[dict[str, str]] = None) -> object:
    payload = urlencode(post).encode("utf-8") if post is not None else None
    req = Request(url, data=payload, headers={"User-Agent": "SignalResearchBot/1.0"})
    with urlopen(req, timeout=15) as res:
        return json.load(res)


def binance_market(symbol: str, now: Optional[float] = None) -> Market:
    if symbol not in ("BTC", "ETH"):
        raise ValueError("Supported symbols: BTC, ETH")
    pair = f"{symbol}USDT"
    base = os.environ.get("BINANCE_MARKET_URL", "https://data-api.binance.vision").rstrip("/")
    url = f"{base}/api/v3/klines?{urlencode({'symbol': pair, 'interval': '1m', 'limit': 360})}"
    rows = http_json(url)
    if not isinstance(rows, list):
        raise ValueError(f"Binance invalid response for {pair}: {str(rows)[:160]}")
    now = time.time() if now is None else now
    closed: list[Candle] = []
    live: Optional[float] = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError("Malformed Binance kline")
        minute = int(row[0]) // 60_000
        c = Candle(minute, float(row[2]), float(row[3]), float(row[4]))
        if int(row[6]) <= now * 1000:
            closed.append(c)
        else:
            live = c.close
    if len(closed) < 75:
        raise ValueError(f"Binance {pair}: too few CLOSED candles: {len(closed)}")
    return Market(symbol, f"{symbol}/USDT", "Binance", tuple(closed), live or closed[-1].close)


def kraken_market(symbol: str, now: Optional[float] = None) -> Market:
    # Kraken REST OHLC pairs are quoted in USD (not USDT). NEVER blend with Binance.
    pair_api = {"BTC": "XBTUSD", "ETH": "ETHUSD"}[symbol]
    url = "https://api.kraken.com/0/public/OHLC?" + urlencode({"pair": pair_api, "interval": 1})
    data = http_json(url)
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError(f"Kraken error for {pair_api}: {str(data)[:160]}")
    result = data.get("result") or {}
    names = [k for k in result if k != "last"]
    if len(names) != 1:
        raise ValueError(f"Unexpected Kraken OHLC keys: {names}")
    rows = result[names[0]]
    now = time.time() if now is None else now
    minute_now = int(now // 60)
    candles: list[Candle] = []
    for r in rows:
        if int(r[0]) // 60 < minute_now:
            candles.append(Candle(int(r[0]) // 60, float(r[2]), float(r[3]), float(r[4])))
    if len(candles) < 75:
        raise ValueError(f"Kraken {pair_api}: too few CLOSED candles: {len(candles)}")
    live = float(rows[-1][4])
    return Market(symbol, f"{symbol}/USD", "Kraken", tuple(candles), live)


def market(symbol: str) -> Market:
    # Source-specific pair names are mapped separately; both use real candles.
    try:
        return binance_market(symbol)
    except (HTTPError, URLError, ValueError, TimeoutError, OSError) as exc:
        LOG.warning("Binance %s unavailable: %s. Trying Kraken USD...", symbol, exc)
    return kraken_market(symbol)


def pivots(candles: tuple[Candle, ...], radius: int) -> list[int]:
    if radius < 1:
        raise ValueError("PIVOT_RADIUS must be 1 or higher")
    idx: list[int] = []
    for i in range(radius, len(candles) - radius):
        low = candles[i].low
        if (all(low < c.low for c in candles[i-radius:i]) and
                all(low <= c.low for c in candles[i+1:i+radius+1])):
            idx.append(i)
    return idx


def newest_candidate(candles: tuple[Candle, ...], rule: Rule) -> Optional[Candidate]:
    """Select latest confirmed two-minimum pair; no bounce/5-bar extra filters.

    A pair is defined using only closed candles available *now*. The third touch
    is NOT selected from future history: it is detected later by the live watch.
    """
    ps = pivots(candles, rule.pivot_radius)
    if not ps:
        return None
    for b in reversed(ps):
        if candles[-1].minute - candles[b].minute > rule.max_age_min:
            break
        for a in reversed(ps):
            if a >= b:
                continue
            gap = candles[b].minute - candles[a].minute
            if gap > rule.max_gap:
                break
            if gap >= rule.min_gap:
                return Candidate(candles[a], candles[b], (candles[a].low + candles[b].low)/2)
    return None


def warning_level(c: Candidate, rule: Rule) -> float:
    return c.level * (1 + rule.warning_buffer_pct / 100)


def advance(w: Watch, m: Market, c: Optional[Candidate], rule: Rule) -> list[str]:
    """Return NEW events (warning/entry), without replay on initialization.

    Entry is first observation at/below yellow level AFTER price has previously
    been seen above the purple line, following both reference lows. No future
    candles or invented rebound confirmation are used.
    """
    if c is None:
        w.key = ""
        w.armed = False
        return []
    source_key = f"{m.exchange}|{m.pair}"
    key = source_key + "|" + c.key
    above_warning = m.price > warning_level(c, rule)
    if w.key != key:
        w.key = key
        w.source_key = source_key
        w.armed = above_warning
        w.warned = False
        w.entered = False
        # Do not emit old signals just because bot was deployed / restarted.
        return []
    if above_warning:
        w.armed = True
    if not w.armed or w.entered:
        return []
    if m.price <= c.level:
        w.entered = True
        return ["entry"]
    if m.price <= warning_level(c, rule) and not w.warned:
        w.warned = True
        return ["warning"]
    return []


def fmt_minute(minute: int) -> str:
    return datetime.fromtimestamp(minute * 60, tz=timezone.utc).strftime("%d.%m %H:%M UTC")


def message(event: str, m: Market, c: Candidate, rule: Rule) -> str:
    status = {"warning": "⚠️ ПРЕДУПРЕЖДЕНИЕ: приближение к уровню",
              "entry": "🟢 КАНДИДАТ НА ВХОД: касание уровня"}[event]
    return (f"{status}\n"
            f"Актив: {m.pair} | {m.exchange}\n"
            f"Цена сейчас: {m.price:.6f}\n"
            f"Первый минимум: {fmt_minute(c.first.minute)} — {c.first.low:.6f}\n"
            f"Второй минимум: {fmt_minute(c.second.minute)} — {c.second.low:.6f}\n"
            f"Интервал: {c.second.minute-c.first.minute} мин\n"
            f"Жёлтая линия: {c.level:.6f}\n"
            f"Порог предупреждения*: {warning_level(c, rule):.6f}\n"
            "*Порог предупреждения предварительный; заказчик ещё не задал его формулу.\n"
            "Сигнал по рабочей интерпретации ТЗ, без автоматической сделки.")


def telegram_send(token: str, chat_id: str, text: str) -> None:
    answer = http_json(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat_id, "text": text})
    if not isinstance(answer, dict) or not answer.get("ok"):
        raise RuntimeError("Telegram did not confirm message delivery")


def telegram_chat_ids(token: str) -> list[str]:
    response = http_json(f"https://api.telegram.org/bot{token}/getUpdates")
    if not isinstance(response, dict) or not response.get("ok"):
        raise RuntimeError("Unable to read Telegram updates")
    return sorted({str(entry["message"]["chat"]["id"])
                   for entry in response.get("result", []) if "message" in entry})


def one_pass(symbol: str, rule: Rule) -> None:
    m = market(symbol)
    c = newest_candidate(m.candles, rule)
    print(f"{m.exchange} / {m.pair}: {len(m.candles)} закрытых минутных свечей, ",
          f"последняя цена {m.price:.6f}")
    if c:
        print(f"Пара минимумов {fmt_minute(c.first.minute)} / {fmt_minute(c.second.minute)}; "
              f"жёлтый уровень {c.level:.6f}, предупреждение* {warning_level(c,rule):.6f}")
        print("*Не торговый сигнал: это просмотр уже существующих данных.")
    else:
        print("Подходящей пары минимумов сейчас нет")


def run_monitor(symbols: list[str], rule: Rule, poll_seconds: int, *,
                dry_run: bool, token: str, chat_id: str,
                fetch: Callable[[str], Market] = market,
                send: Callable[[str, str, str], None] = telegram_send,
                stop_after: int = 0) -> None:
    state = {symbol: Watch() for symbol in symbols}
    print(f"Signal запущен: {', '.join(symbols)}; опрос каждые {poll_seconds} с; "
          f"отправка Telegram: {'выключена' if dry_run else 'включена'}", flush=True)
    polls = 0
    while True:
        for symbol in symbols:
            try:
                m = fetch(symbol)
                minutes_old = time.time() / 60 - m.candles[-1].minute
                if minutes_old > 4 or minutes_old < 0:
                    raise RuntimeError(f"Stale/future candles: last closed minute {minutes_old:.1f} min old")
                c = newest_candidate(m.candles, rule)
                w = state[symbol]
                # When we have a fresh complete candle, print current state.
                if w.last_candle != m.candles[-1].minute:
                    LOG.info("%s %s price=%.6f candidate=%s", m.exchange, m.pair, m.price,
                             c.key if c else "none")
                    w.last_candle = m.candles[-1].minute
                # Prepare event, but ONLY mark it sent AFTER successful delivery.
                backup = Watch(**vars(w))
                events = advance(w, m, c, rule)
                for event in events:
                    if c is None:
                        continue
                    alert = message(event, m, c, rule)
                    if dry_run:
                        print("[TEST, not sent]\n" + alert + "\n", flush=True)
                    else:
                        try:
                            send(token, chat_id, alert)
                            LOG.info("Telegram delivered %s for %s", event, m.pair)
                        except Exception:
                            # Next poll retries instead of silently losing a failed alert.
                            state[symbol] = backup
                            raise
            except Exception as exc:
                LOG.error("%s: %s: %s", symbol, type(exc).__name__, exc)
        polls += 1
        if stop_after and polls >= stop_after:
            return
        time.sleep(poll_seconds)


def fake_demo(rule: Rule) -> None:
    """Demonstrate status machine WITHOUT exchange or Telegram."""
    k = 20_000_000
    ca = Candle(k, 101, 100, 101)
    cb = Candle(k+38, 103, 101, 102)
    candidate = Candidate(ca, cb, (ca.low+cb.low)/2)
    state = Watch()
    dummy = (Candle(k+40, 103, 102, 102),)
    for price in (103, 101.0, 100.60, 100.45, 99.9):
        m = Market("BTC", "BTC/USDT", "DEMO", dummy, price)
        events = advance(state, m, candidate, rule)
        print(f"ДЕМО: цена {price:.2f}, события: {events or 'нет'}")
        for event in events:
            print(message(event, m, candidate, rule), "\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BTC/ETH real-time three-touch prototype → Telegram alerts")
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--monitor", action="store_true", help="run continuous Telegram alert monitor")
    modes.add_argument("--once", action="store_true", help="show latest market snapshot, no alerts")
    modes.add_argument("--demo", action="store_true", help="offline simulation, no alerts")
    modes.add_argument("--telegram-test", action="store_true", help="send ONE Telegram test message")
    modes.add_argument("--chat-id", action="store_true", help="show Telegram chat ID from /start messages")
    p.add_argument("--symbols", default=os.environ.get("SYMBOLS", "BTC,ETH"))
    p.add_argument("--poll-seconds", type=int, default=int(os.environ.get("POLL_SECONDS", "20")))
    p.add_argument("--dry-run", action="store_true", help="disable Telegram sending")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols or any(s not in ("BTC", "ETH") for s in symbols):
        raise SystemExit("--symbols must be BTC, ETH or BTC,ETH")
    rule = Rule(warning_buffer_pct=float(os.environ.get("WARNING_BUFFER_PCT", "0.15")),
                pivot_radius=int(os.environ.get("PIVOT_RADIUS", "2")))
    if rule.warning_buffer_pct < 0 or rule.pivot_radius < 1:
        raise SystemExit("Invalid WARNING_BUFFER_PCT or PIVOT_RADIUS")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if args.demo:
        fake_demo(rule)
    elif args.once:
        for symbol in symbols:
            try:
                one_pass(symbol, rule)
            except Exception as exc:
                LOG.error("%s: %s: %s", symbol, type(exc).__name__, exc)
    elif args.chat_id:
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN must be set")
        print("Telegram chat IDs:", telegram_chat_ids(token))
    elif args.telegram_test:
        if not token or not chat_id:
            raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        telegram_send(token, chat_id, "✅ Signal связан с Telegram. Это тест, а не торговый сигнал.")
        print("Telegram test delivered")
    else:
        if args.poll_seconds < 10:
            raise SystemExit("Use --poll-seconds >= 10")
        if not args.dry_run and (not token or not chat_id):
            raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID or use --dry-run")
        run_monitor(symbols, rule, args.poll_seconds, dry_run=args.dry_run, token=token, chat_id=chat_id)


if __name__ == "__main__":
    main()
