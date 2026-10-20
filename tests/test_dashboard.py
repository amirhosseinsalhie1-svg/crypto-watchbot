from dataclasses import replace
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from watchbot.control import queue_control, consume_controls
from watchbot.core import Config, INTERVALS, parse_time
from watchbot.dashboard import make_server, snapshot
from watchbot.demo import fixture
from watchbot.engine import Engine
from watchbot.journal import Journal

NOW = parse_time('2026-10-20T12:00:00Z')


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Config(symbols=('BTCUSDT',), database=str(self.root/'journal.sqlite3'),
                          output_dir=str(self.root/'charts'), news_file=str(self.root/'news.json'))

    def engine(self):
        e = Engine(self.cfg)
        self.addCleanup(e.journal.close)
        return e

    def test_missing_journal_read_does_not_create_database(self):
        result = snapshot(self.cfg)
        self.assertEqual(result['records'], [])
        self.assertEqual(result['health']['status'], 'NO_JOURNAL')
        self.assertFalse(Path(self.cfg.database).exists())

    def test_recorded_inputs_are_immutable_exact_and_closed(self):
        engine = self.engine()
        frames = {tf: fixture(NOW,tf) for tf in INTERVALS}
        precise = float(frames['5m'].iloc[0].open)
        engine.process(NOW, {'BTCUSDT':frames},render_signals=False,queue_alerts=False)
        first = snapshot(self.cfg)['records'][0]
        self.assertEqual(first['observed_at'], NOW)
        self.assertEqual(first['frames']['5m'][0]['open'], precise)
        self.assertTrue(all(b['close_time'] < NOW for rows in first['frames'].values() for b in rows))
        engine.process(NOW+1000,{'BTCUSDT':frames},render_signals=False,queue_alerts=False)
        self.assertEqual(snapshot(self.cfg)['records'][0],first)

    def test_legacy_analysis_does_not_fabricate_evidence(self):
        engine = self.engine()
        from watchbot.core import analyze
        a,_ = analyze('BTCUSDT',{tf:fixture(NOW,tf) for tf in INTERVALS},NOW,self.cfg)
        with engine.journal.transaction():
            engine.journal.record(a)
        r=snapshot(self.cfg)['records'][0]
        self.assertEqual(r['evidence_status'],'LEGACY_NO_INPUTS')
        self.assertIsNone(r['observed_at'])
        self.assertEqual(r['frames'],{})

    def test_later_trade_does_not_enter_earlier_known_trade_set(self):
        e=self.engine()
        e.process(NOW,{'BTCUSDT':{tf:fixture(NOW,tf) for tf in INTERVALS}},render_signals=False)
        with e.journal.transaction():
            e.journal.db.execute("INSERT INTO trades(id,symbol,side,signal_at,entry_at,entry,stop,target,qty) VALUES(?,?,?,?,?,?,?,?,?)",
                                 ('later','BTCUSDT','LONG',NOW,NOW,100,99,102,1))
        result=snapshot(self.cfg)
        self.assertEqual(result['records'][0]['risk']['known_trade_ids'],[])
        self.assertEqual(result['trades'][0]['id'],'later')

    def test_configuration_mismatch_is_visible_without_mutating_journal(self):
        engine = self.engine()
        changed=replace(self.cfg,risk_fraction=.003)
        self.assertFalse(snapshot(changed)['config_matches'])
        self.assertEqual(engine.journal.get('config_hash'),self.cfg.fingerprint())

    def test_heartbeat_freshness_does_not_follow_analysis_count(self):
        self.engine()
        self.assertEqual(snapshot(self.cfg)['health']['status'],'NO_HEARTBEAT')
        (self.root/'heartbeat.json').write_text(json.dumps({'last_success_ms':NOW}))
        with patch('watchbot.dashboard.utc_ms',return_value=NOW+1000):
            self.assertEqual(snapshot(self.cfg)['health']['status'],'FRESH')
        with patch('watchbot.dashboard.utc_ms',return_value=NOW+120000):
            self.assertEqual(snapshot(self.cfg)['health']['status'],'STALE')

    def test_control_is_queued_then_applied_by_scanner_only(self):
        engine=self.engine()
        with engine.journal.transaction():
            engine.journal.put('pending',{'fake':'pending'})
        with patch('watchbot.control.utc_ms',return_value=NOW):
            command=queue_control(self.cfg,'pause',self.cfg.fingerprint())
        self.assertFalse(engine.journal.get('paused',False))
        consume_controls(engine.journal,NOW+1000)
        self.assertTrue(engine.journal.get('paused'))
        self.assertIsNone(engine.journal.get('pending'))
        event=engine.journal.db.execute("SELECT * FROM events WHERE kind='CONTROL_APPLIED'").fetchone()
        self.assertEqual(json.loads(event['payload'])['id'],command['id'])
        consume_controls(engine.journal,NOW+2000)
        self.assertEqual(engine.journal.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],1)

    def test_expired_control_does_not_change_state(self):
        e=self.engine()
        with patch('watchbot.control.utc_ms',return_value=NOW):queue_control(self.cfg,'pause',self.cfg.fingerprint())
        consume_controls(e.journal,NOW+121000)
        self.assertFalse(e.journal.get('paused',False))
        self.assertEqual(e.journal.db.execute('SELECT kind FROM events').fetchone()[0],'CONTROL_REJECTED')

    def test_unsupported_control_and_config_are_rejected(self):
        with self.assertRaises(ValueError):queue_control(self.cfg,'buy',self.cfg.fingerprint())
        with self.assertRaises(ValueError):queue_control(self.cfg,'pause','wrong')

    def test_http_session_origin_and_csrf_and_static_assets(self):
        e=self.engine()
        server=make_server(self.cfg,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            def call(method,path,body=None,headers=None):
                c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                c.request(method,path,body=body,headers=headers or {})
                response=c.getresponse();status=response.status;content=response.read();c.close()
                return status,content
            for path in ['/','/app.js','/model.js','/app.css','/api/snapshot']:
                self.assertEqual(call('GET',path)[0],200)
            self.assertEqual(call('GET','/../config.json')[0],404)
            self.assertEqual(call('GET','/api/session',headers={'Host':'evil.example'})[0],403)
            session=json.loads(call('GET','/api/session')[1])
            payload=json.dumps({'action':'pause','config_hash':self.cfg.fingerprint()})
            self.assertEqual(call('POST','/api/control',payload,{'Content-Type':'application/json'})[0],403)
            headers={'Content-Type':'application/json','X-Watchbot-Token':session['token'],'Origin':'https://evil.example'}
            self.assertEqual(call('POST','/api/control',payload,headers)[0],403)
            headers.pop('Origin')
            self.assertEqual(call('POST','/api/control',payload,headers)[0],202)
            self.assertFalse(e.journal.get('paused',False))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)
