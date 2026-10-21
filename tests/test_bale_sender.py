import os
import unittest
from unittest.mock import patch
from watchbot.io import Telegram

class BaleSenderTests(unittest.TestCase):
    def test_bale_overrides_stale_telegram_relay_and_does_not_poll(self):
        with patch.dict(os.environ, {'BALE_SENDER_TOKEN':'bale-valid-token', 'BALE_CHANNEL_ID':'4869628883', 'TELEGRAM_RELAY_URL':'https://stale.example'}, clear=True):
            sender = Telegram()
            with patch('watchbot.io.request_json', return_value={'ok':True,'result':{'message_id':3}}) as call:
                sender.text('hello')
                self.assertEqual(call.call_args.args[0], 'https://tapi.bale.ai/botbale-valid-token/sendMessage')
                self.assertTrue(call.call_args.kwargs['direct'])
                call.reset_mock()
                sender.commands(None)
                call.assert_not_called()
    def test_partial_bale_configuration_rejected(self):
        with patch.dict(os.environ, {'BALE_SENDER_TOKEN':'token'}, clear=True):
            with self.assertRaises(ValueError): Telegram()
