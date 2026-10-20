import test from 'node:test';
import assert from 'node:assert/strict';
import {liveChartRows} from '../watchbot/web/market-model.js';

const recorded=[{open_time:0,close_time:299999,open:100,high:102,low:99,close:101,volume:10}];
const bar={open_time:300000,close_time:599999,open:101,high:103,low:100,close:102,volume:2};
const market={config_hash:'experiment',receivedAt:1000,symbols:{BTCUSDT:{fresh:true,age_ms:100,bar}}};
const options={mode:'local',view:'workspace',selected:null,tf:'5m',symbol:'BTCUSDT',configHash:'experiment',now:1100};

test('live chart appends a display bar without mutating recorded evidence',()=>{
  const rows=liveChartRows(recorded,market,options);
  assert.equal(rows.length,2);assert.equal(recorded.length,1);
  rows[1].close=999;assert.equal(market.symbols.BTCUSDT.bar.close,102);
});
test('replay, imported files, selected evidence and higher timeframes never get live data',()=>{
  for(const override of [{view:'replay'},{mode:'import'},{selected:7},{tf:'4h'},{configHash:'other'}])
    assert.equal(liveChartRows(recorded,market,{...options,...override}),null);
});
test('stale display data and feed gaps are rejected',()=>{
  assert.equal(liveChartRows(recorded,market,{...options,now:18000}),null);
  const gap={...market,symbols:{BTCUSDT:{fresh:true,age_ms:100,bar:{...bar,open_time:900000}}}};
  assert.equal(liveChartRows(recorded,gap,options),null);
});
test('quote-only mode displays price without inventing historical evidence',()=>{
  assert.equal(liveChartRows([],market,options).length,1);
  assert.equal(liveChartRows([],market,{...options,view:'replay'}),null);
});
