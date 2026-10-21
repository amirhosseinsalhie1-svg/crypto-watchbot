import unittest
from dataclasses import replace
from unittest.mock import patch
from watchbot.core import Config, INTERVALS
from watchbot.demo import fixture
from watchbot.io import MexcReference, NetworkError

class MexcTests(unittest.TestCase):
    def setUp(self):
        self.now = 1792584001000
        self.cfg = replace(Config(), source='mexc-usdt-perpetual', symbols=('BTCUSDT',))
        self.provider = MexcReference(self.cfg)

    def response(self, url, **kwargs):
        self.assertTrue(kwargs['direct'])
        if '/ping?' in url:
            return dict(success=True, code=0, data=self.now)
        tf = next(tf for tf, period in self.provider.PERIODS.items() if 'interval='+period in url)
        f = fixture(self.now, tf)
        d = {k: f[col].tolist() for k, col in [('open','open'),('high','high'),('low','low'),('close','close'),('vol','volume')]}
        d['time'] = (f.open_time // 1000).tolist()
        # Provider may include an unfinished candle even with an end bound.
        for k in d:
            d[k].append(d[k][-1] + INTERVALS[tf]//1000 if k == 'time' else d[k][-1])
        return dict(success=True, code=0, data=d)

    @patch('watchbot.io.utc_ms')
    @patch('watchbot.io.request_json')
    def test_closed_candles_units_mapping_and_cache(self, request, clock):
        clock.return_value = self.now
        request.side_effect = self.response
        now, data = self.provider.snapshot(self.cfg.symbols)
        for tf, duration in INTERVALS.items():
            f = data['BTCUSDT'][tf]
            self.assertEqual(len(f), 300)
            self.assertEqual(int(f.iloc[-1].close_time), now//duration*duration-1)
        self.assertEqual(request.call_count, 5)
        self.provider.snapshot(self.cfg.symbols)
        self.assertEqual(request.call_count, 7)

    @patch('watchbot.io.utc_ms')
    @patch('watchbot.io.request_json')
    def test_clock_skew_rejected(self, request, clock):
        clock.return_value = self.now+20000
        request.return_value = dict(success=True, code=0, data=self.now)
        with self.assertRaises(NetworkError):
            self.provider.snapshot(self.cfg.symbols)

    @patch('watchbot.io.request_json')
    def test_enabled_perpetuals_only(self, request):
        rows = [dict(symbol='BTC_USDT',state=0,futureType=1,quoteCoin='USDT',settleCoin='USDT'),
                dict(symbol='BAD_USDT',state=3,futureType=1,quoteCoin='USDT',settleCoin='USDT')]
        request.return_value = dict(success=True,code=0,data=rows)
        self.assertEqual(self.provider.available_symbols(), {'BTCUSDT'})
