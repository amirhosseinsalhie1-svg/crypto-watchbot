// Live display data is never merged into saved evidence or used during replay.
export function liveChartRows(recorded, market, {mode,view,selected,tf,symbol,configHash,now=Date.now()}) {
  const r=market?.symbols?.[symbol];
  if(mode!=='local'||view!=='workspace'||selected!=null||tf!=='5m'||market?.config_hash!==configHash||!r?.fresh)return null;
  const age=r.age_ms+(now-market.receivedAt),b=r.bar,last=recorded.at(-1);
  if(!Number.isFinite(age)||age<0||age>15000||![b?.open_time,b?.close_time,b?.open,b?.high,b?.low,b?.close,b?.volume].every(Number.isFinite))return null;
  if(!last)return [{...b}];
  // A feed gap needs a finalized scanner catch-up, never a fabricated chart bridge.
  if(b.open_time<last.open_time||b.open_time>last.open_time+300000)return null;
  return b.open_time===last.open_time?[...recorded.slice(0,-1),{...b}]:[...recorded,{...b}];
}
