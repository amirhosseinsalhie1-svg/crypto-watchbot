import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from watchbot.io import Telegram

class RelayTests(unittest.TestCase):
    def relay(self, **extra):
        with patch.dict(os.environ, dict(TELEGRAM_RELAY_URL='https://relay.example',
                                        TELEGRAM_RELAY_KEY='x'*40, TELEGRAM_CHAT_ID='123', **extra), clear=True):
            return Telegram()

    @patch('watchbot.io.request_json', return_value={'ok':True,'result':[]})
    def test_token_stays_off_client_and_direct_authenticated_route(self, request):
        tg = self.relay()
        self.assertTrue(tg.configured)
        self.assertEqual(tg.call('getUpdates',{'timeout':0}), [])
        self.assertEqual(request.call_args.args[0],'https://relay.example/telegram/getUpdates')
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'],'Bearer '+'x'*40)
        self.assertTrue(request.call_args.kwargs['direct'])

    @patch('watchbot.io.request_json', return_value={'ok':True,'result':{}})
    def test_photo_uses_same_authenticated_relay(self, request):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'chart.png';p.write_bytes(b'chart bytes')
            self.relay().photo(str(p),'caption')
        self.assertEqual(request.call_args.args[0],'https://relay.example/telegram/sendPhoto')
        self.assertIn(b'chart bytes',request.call_args.kwargs['data'])

    def test_relay_url_rejects_credentials_or_queries(self):
        for url in ['http://relay.example','https://user:pass@relay.example','https://relay.example?secret=x']:
            with patch.dict(os.environ,{'TELEGRAM_RELAY_URL':url,'TELEGRAM_RELAY_KEY':'x'*40},clear=True):
                with self.assertRaises(ValueError): Telegram()

    def test_invalid_control_character_never_reaches_url(self):
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'\x16'},clear=True): tg=Telegram()
        with self.assertRaises(ValueError) as error: tg.call('getMe',{})
        self.assertNotIn('\x16',str(error.exception))
