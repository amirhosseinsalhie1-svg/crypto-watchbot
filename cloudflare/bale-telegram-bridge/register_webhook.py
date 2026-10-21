"""Register through Bale without revealing the webhook secret in output."""
import json
import os
import sys
import urllib.parse
import urllib.request

try:
    token = os.environ['BALE_RECEIVER_TOKEN'].strip()
    key = os.environ['BALE_WEBHOOK_KEY'].strip()
    origin = os.environ['BALE_BRIDGE_URL'].strip().rstrip('/')
    parsed = urllib.parse.urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.path or
            parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError()
    if len(key) < 32 or not all(c.isalnum() or c in '-_' for c in key):
        raise ValueError()
    if not token.isascii() or any(ord(c) <= 32 or c in '/?#' for c in token):
        raise ValueError()
    payload = urllib.parse.urlencode({'url': origin + '/bale/' + key}).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('https://tapi.bale.ai/bot' + token + '/setWebhook',
                     data=payload, timeout=20) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise ValueError()
    print('Webhook registered. Send TEST-BRIDGE-1 in the Bale channel.')
except Exception:
    print('Webhook registration failed. Check environment values and Bale access.')
    sys.exit(1)
