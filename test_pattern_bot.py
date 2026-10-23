import unittest
from unittest.mock import patch
import time
import pattern_bot as bot


def candles_with_dips(gap=38, base=100, start=None):
    start = int(time.time() // 60) - 150 if start is None else start
    c = [bot.Candle(start+i, base+1, base, base+0.4) for i in range(140)]
    # isolated, confirmed local lows; make 2nd slightly higher than 1st.
    c[20] = bot.Candle(start+20, base+1, base-3, base)
    c[20+gap] = bot.Candle(start+20+gap, base+1, base-2, base)
    return tuple(c)


class SignalTests(unittest.TestCase):
    def test_midpoint_and_38min(self):
        c = candles_with_dips()
        p = bot.newest_candidate(c, bot.Rule())
        self.assertIsNotNone(p)
        self.assertEqual(p.second.minute-p.first.minute, 38)
        self.assertEqual(p.level, 97.5)

    def test_second_can_be_lower(self):
        c = list(candles_with_dips())
        i = 58
        c[i] = bot.Candle(c[i].minute, 101, 95, 100)
        p = bot.newest_candidate(tuple(c), bot.Rule())
        self.assertIsNotNone(p)
        self.assertEqual(p.level, 96)

    def test_gap_29_rejected(self):
        self.assertIsNone(bot.newest_candidate(candles_with_dips(gap=29), bot.Rule()))

    def test_gap_46_rejected(self):
        self.assertIsNone(bot.newest_candidate(candles_with_dips(gap=46), bot.Rule()))

    def test_gap_30_45_inclusive(self):
        for gap in (30, 45):
            with self.subTest(gap=gap):
                self.assertIsNotNone(bot.newest_candidate(candles_with_dips(gap), bot.Rule()))

    def test_no_historical_alarm_on_first_poll(self):
        cand = bot.newest_candidate(candles_with_dips(), bot.Rule())
        w = bot.Watch()
        mk = bot.Market('BTC', 'BTC/USDT', 'Binance', candles_with_dips(), 90)
        self.assertEqual(bot.advance(w, mk, cand, bot.Rule()), [])

    def test_warning_then_entry_and_no_repeat(self):
        cand = bot.newest_candidate(candles_with_dips(), bot.Rule())
        w = bot.Watch()
        mk = lambda price: bot.Market('BTC', 'BTC/USDT', 'Binance', (), price)
        rule = bot.Rule()
        self.assertEqual(bot.advance(w, mk(102), cand, rule), [])
        self.assertEqual(bot.advance(w, mk(bot.warning_level(cand,rule) - 0.01), cand, rule), ['warning'])
        self.assertEqual(bot.advance(w, mk(cand.level), cand, rule), ['entry'])
        self.assertEqual(bot.advance(w, mk(cand.level-1), cand, rule), [])

    def test_no_entry_until_armed_from_above(self):
        cand = bot.newest_candidate(candles_with_dips(), bot.Rule())
        w = bot.Watch()
        mk = lambda price: bot.Market('BTC', 'BTC/USDT', 'Binance', (), price)
        self.assertEqual(bot.advance(w, mk(90), cand, bot.Rule()), [])
        self.assertEqual(bot.advance(w, mk(89), cand, bot.Rule()), [])
        self.assertEqual(bot.advance(w, mk(103), cand, bot.Rule()), [])
        self.assertEqual(bot.advance(w, mk(97), cand, bot.Rule()), ['entry'])

    def test_no_pivot_lookahead(self):
        cs = list(candles_with_dips())[:59]  # after dip 58, no 2 closed confirmation bars
        self.assertIsNone(bot.newest_candidate(tuple(cs), bot.Rule()))
        cs = list(candles_with_dips())[:61]
        self.assertIsNotNone(bot.newest_candidate(tuple(cs), bot.Rule()))

    def test_kraken_symbol_mapping_different_for_btc_eth(self):
        seen = []
        now = int(time.time() // 60)*60 + 20
        def fake(url, post=None):
            seen.append(url)
            key = 'XXBTZUSD' if 'XBTUSD' in url else 'XETHZUSD'
            vals = [[now - (120-i)*60, '0', '101', '99', '100','0','0','0'] for i in range(121)]
            return {'error': [], 'result': {key: vals, 'last': 'x'}}
        with patch.object(bot, 'http_json', side_effect=fake):
            b = bot.kraken_market('BTC', now)
            e = bot.kraken_market('ETH', now)
        self.assertIn('pair=XBTUSD', seen[0])
        self.assertIn('pair=ETHUSD', seen[1])
        self.assertEqual(b.pair, 'BTC/USD')
        self.assertEqual(e.pair, 'ETH/USD')

    def test_binance_symbol_mapping_different(self):
        seen=[]
        now = int(time.time()//60)*60 + 20
        def fake(url, post=None):
            seen.append(url)
            rows=[]
            for i in range(121):
                t=int((now-(120-i)*60)*1000)
                rows.append([t, '100', '102','98','101','0',t+59999])
            return rows
        with patch.object(bot, 'http_json', side_effect=fake):
            b = bot.binance_market('BTC', now)
            e = bot.binance_market('ETH', now)
        self.assertIn('BTCUSDT', seen[0]); self.assertIn('ETHUSDT', seen[1])
        self.assertEqual(b.pair,'BTC/USDT'); self.assertEqual(e.pair,'ETH/USDT')
        self.assertEqual(b.candles[-1].minute, e.candles[-1].minute)

    def test_unsupported_pair(self):
        with self.assertRaises(ValueError):
            bot.binance_market('SOL')


if __name__ == '__main__':
    unittest.main(verbosity=2)
