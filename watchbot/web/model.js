export const LABELS={TECHNICAL_CONFIRMED:'تأییدشده',PENDING:'در انتظار محرک',NO_SETUP:'بدون ستاپ',BLOCKED_DATA:'دادهٔ ناکافی',BLOCKED_RISK:'ریسک نامعتبر',EXPIRED:'منقضی',INVALIDATED:'باطل‌شده'};
export const timeOf=r=>r.observed_at??r.analysis.asof;
export function validateSnapshot(d){
 if(!d||d.kind!=='watchbot-dashboard-snapshot'||d.schema_version!==1||!Array.isArray(d.records)||d.records.length>1000||!Array.isArray(d.symbols)||d.symbols.length>100)throw Error('فایل باید خروجی dashboard-export ربات باشد.');
 const ids=new Set();for(const r of d.records){if(!Number.isSafeInteger(r.id)||ids.has(r.id)||!r.analysis||typeof r.analysis.symbol!=='string'||!Number.isFinite(r.analysis.asof)||!(r.analysis.status in LABELS)||!Number.isFinite(timeOf(r)))throw Error('ساختار تصمیم‌های فایل معتبر نیست.');ids.add(r.id);
  for(const [tf,rows]of Object.entries(r.frames??{})){if(!['5m','4h','1d'].includes(tf)||!Array.isArray(rows)||rows.length>1000)throw Error('تاریخچهٔ نامعتبر');let last=-Infinity;for(const b of rows){if(!['open_time','close_time','open','high','low','close'].every(k=>Number.isFinite(b[k]))||b.open_time<=last||b.close_time>=timeOf(r)||b.high<Math.max(b.open,b.close,b.low)||b.low>Math.min(b.open,b.close,b.high))throw Error('کندل نامعتبر یا متعلق به آینده در فایل');last=b.open_time;}}
 }return d;
}
export function visibleRecords(d,cutoff=Infinity,includeLegacy=cutoff===Infinity){return d.records.filter(r=>(includeLegacy||r.observed_at!=null)&&timeOf(r)<=cutoff).sort((a,b)=>timeOf(a)-timeOf(b)||a.id-b.id);}
export function latestFor(d,symbol,cutoff=Infinity,includeLegacy=cutoff===Infinity){return visibleRecords(d,cutoff,includeLegacy).filter(r=>r.analysis.symbol===symbol).at(-1)??null;}
export function visibleTrades(d,record,cutoff,replaying){
 const known=new Set(record?.risk?.known_trade_ids??[]),closed=new Set(record?.risk?.known_closed_trade_ids??[]);
 return (d.trades??[]).filter(t=>t.entry_at<=cutoff&&(!replaying||known.has(t.id))).map(t=>{
  const outcome=t.exit_at!=null&&t.exit_at<=cutoff&&(!replaying||closed.has(t.id));
  return {...t,exit_at:outcome?t.exit_at:null,exit:outcome?t.exit:null,pnl:outcome?t.pnl:null,reason:outcome?t.reason:null};
 });
}
export function lifecycle(record,records,cutoff){
 const a=record.analysis;if(a.status!=='TECHNICAL_CONFIRMED')return a.status;
 // This is alert validity, never a fabricated paper fill or a KX setup lifecycle.
 const end=a.asof+300000;
 for(const r of records){if(r.analysis.symbol!==a.symbol||timeOf(r)>cutoff)continue;
  for(const b of r.frames?.['5m']??[]){if(b.open_time<a.asof||b.close_time>=cutoff||b.open_time>=end)continue;
   if((a.side==='LONG'&&b.low<=a.stop)||(a.side==='SHORT'&&b.high>=a.stop))return 'INVALIDATED';}}
 return cutoff>=end?'EXPIRED':'TECHNICAL_CONFIRMED';
}
export function answerQuestion(record,question){
 if(!record)return {text:'هنوز تصمیمی با شواهد ثبت‌شده برای پاسخ‌دادن وجود ندارد.',fields:[]};
 const a=record.analysis,q=question.trim(),base=`${a.symbol} · ${LABELS[a.status]??a.status}`;
 if(!q)return {text:'سؤالت را دربارهٔ تصمیم انتخاب‌شده بنویس',fields:[]};
 if(/فردا|پیش.?بینی|tomorrow|predict/i.test(q))return {text:'این بخش فقط شواهد تصمیم‌های ثبت‌شده را توضیح می‌دهد و قیمت آینده را پیش‌بینی نمی‌کند',fields:[]};
 if(/خبر|فاندامنتال|news/i.test(q))return {text:`وضعیت خبر در همان تصمیم: ${a.news_status} · ${a.news_note??''}\nمنابع ثبت‌شده: ${(a.news_evidence?.sources??[]).join('، ')||'منبعی ثبت نشده است.'}\nاین پاسخ خبر تازه‌ای جست‌وجو نمی‌کند.`,fields:['news_status','news_note','news_evidence']};
 if(/ابطال|ضرر|stop/i.test(q))return {text:a.stop!=null?`حد ضرر سناریوی ${a.side}: ${a.stop} · ${a.counterevidence?.join('؛ ')??''}`:'برای این تصمیم حد ضرری ثبت نشده؛ ستاپ کامل نشده یا داده کافی نبوده',fields:['side','stop','counterevidence']};
 if(/هدف|سود|target/i.test(q))return {text:a.target!=null?`هدف فرضی ثبت‌شده ${a.target} و قیمت ورود ${a.entry} است · این اعداد اجرای واقعی معامله یا تضمین سود نیستند`:'برای این تصمیم هدف فرضی ثبت نشده است.',fields:['entry','target']};
 if(/ریسک|سرمایه|زیان|موجودی/.test(q)){const k=record.risk;if(!k)return {text:'وضعیت ریسک هم‌زمان با این تصمیم در سوابق قدیمی ثبت نشده',fields:[]};return {text:`سرمایهٔ فرضی ثبت‌شده: ${k.equity_usdt} USDT. زیان تجمیعی روز: ${k.daily_gross_losses??0} USDT. معاملات باز: ${k.open_positions??0}. پایش ${k.paused?'متوقف':'فعال'} و معاملات فرضی ${k.paper_enabled?'روشن':'خاموش'} بوده‌اند.`,fields:['risk']};}
 if(/منبع|نسخه|زمان|داده/.test(q))return {text:`منبع: ${a.source}\nنسخهٔ قواعد: ${a.strategy}\nشناسهٔ تنظیمات: ${a.config_hash}\nزمان بازار: ${a.asof}\nزمان ثبت ارزیابی: ${record.observed_at??'ثبت نشده'}\nشواهد ورودی: ${record.evidence_status}`,fields:['source','strategy','config_hash','asof','observed_at','evidence_status']};
 if(/چرا|رد|تأیید|تایید|تایم|شرط|ستاپ|تحلیل|جهت|خلاصه|why/i.test(q))return {text:`${base}\nدلایل ثبت‌شده: ${(a.reasons??[]).join('؛ ')||'دلیلی ثبت نشده'}\nشواهد مخالف و ملاحظات: ${(a.counterevidence??[]).join('؛ ')||'موردی ثبت نشده'}\nخبر: ${a.news_status}. ${a.news_note??''}`,fields:['status','side','reasons','counterevidence','news_status']};
 return {text:'پاسخ این پرسش در شواهد ساختاریافتهٔ این تصمیم نیست · می‌توانم دلیل تأیید یا رد، وضعیت خبر، حد ضرر، هدف، ریسک یا منبع همان تصمیم را توضیح بدهم',fields:[]};
}
