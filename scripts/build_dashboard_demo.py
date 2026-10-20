"""Build a portable synthetic evidence bundle using the real analysis engine."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile

from watchbot.core import Config, INTERVALS, utc_ms
from watchbot.dashboard import snapshot
from watchbot.demo import fixture
from watchbot.engine import Engine


def build(output):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cfg = replace(Config(), source='SYNTHETIC_DEMO_NOT_MARKET_DATA',
                      database=str(root/'demo.sqlite3'),output_dir=str(root/'charts'),
                      news_file=str(root/'unreviewed-news.json'),paper_enabled=False)
        engine = Engine(cfg)
        finish = utc_ms()//300000*300000+1000
        try:
            for step in range(8):
                at = finish-(7-step)*300000
                data = {}
                for i,(symbol,base) in enumerate(zip(cfg.symbols,[184,29,15.4,82980])):
                    f={tf:fixture(at,tf,base=base*(1+step*.0007),short=symbol=='ORDIUSDT') for tf in INTERVALS}
                    phase=(step+i)%4
                    if phase==1:
                        # A valid close that does not break preceding structure.
                        d=f['5m'];idx=len(d)-1
                        mid=float(d.iloc[-2].close)
                        d.loc[idx,['open','high','low','close']]=[mid,mid*1.001,mid*.999,mid]
                    elif phase==2:
                        f['1d'].loc[:,['open','high','low','close']]=[base,base*1.001,base*.999,base]
                    data[symbol]=f
                engine.process(at,data,queue_alerts=False,render_signals=False)
            result=snapshot(cfg)
            result['generated_at']=finish
            result['health']={'status':'SYNTHETIC_DEMO','last_success_ms':None}
            result['demo_notice']='SYNTHETIC FIXTURES. NO LIVE MARKET CONNECTION. NO PERFORMANCE CLAIMS.'
            # Paths belong to a temporary experiment; its hash is retained as evidence.
            Path(output).parent.mkdir(parents=True,exist_ok=True)
            Path(output).write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf8')
            print(json.dumps({'output':str(Path(output).resolve()),'records':len(result['records']),
                              'synthetic':True,'paper_enabled':False}))
        finally:
            engine.journal.close()


if __name__=='__main__':
    build(sys.argv[1] if len(sys.argv)>1 else 'runtime/dashboard-example.json')
