import unittest
from dataclasses import replace
from unittest.mock import patch
from watchbot.core import Config, INTERVALS
from watchbot.demo import fixture
from watchbot.io import ToobitReference, NetworkError
from watchbot.diagnostics import validate_snapshot

class ToobitTests(unittest.TestCase):
    now = 1792584601000

    @patch('watchbot.io.utc_ms')
    @patch('watchbot.io.request_json')
    def test_zero_close_times_open_bar_and_cache(self, request, clock):
        clock.return_value = self.now
        cfg = replace(Config(), source='toobit-usdt-perpetual', symbols=('BTCUSDT',))
        provider = ToobitReference(cfg)
        def response(url, **kwargs):
            self.assertTrue(kwargs['direct'])
            if '/api/v1/time' in url:
                return {'serverTime': self.now}
            self.assertIn('symbol=BTC-SWAP-USDT', url)
            tf = next(tf for tf in INTERVALS if 'interval='+tf in url)
            f = fixture(self.now,tf)
            rows = f[['open_time','open','high','low','close','volume','close_time']].values.tolist()
            # Match the actual API: close time 0, plus an unfinished bar.
            for row in rows:
                row[6] = 0
            row = rows[-1].copy()
            row[0] += INTERVALS[tf]
            rows.append(row)
            return rows
        request.side_effect = response
        now, data = provider.snapshot(cfg.symbols)
        summary = validate_snapshot(cfg,now,data)
        self.assertEqual(summary['BTCUSDT']['5m']['closed_bars'],300)
        self.assertEqual(request.call_count,5)
        provider.snapshot(cfg.symbols)
        self.assertEqual(request.call_count,7)

    @patch('watchbot.io.request_json')
    def test_only_active_linear_contracts(self, request):
        good = dict(symbol='BTC-SWAP-USDT',status='TRADING',inverse=False,marginToken='USDT',quoteAsset='USDT')
        request.return_value = {'contracts':[good, dict(good,status='OFFLINE'), dict(good,inverse=True)]}
        self.assertEqual(ToobitReference(Config()).available_symbols(), {'BTCUSDT'})

    @patch('watchbot.io.request_json', return_value={'code':400})
    def test_error_payload_rejected(self, request):
        with self.assertRaises(NetworkError):
            ToobitReference(Config()).get('/fapi/v1/klines',symbol='BTCUSDT',interval='5m')
