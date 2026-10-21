import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker from '../cloudflare/bale-telegram-bridge/worker.mjs';
const env = () => {const sent = new Map(); return {WEBHOOK_KEY:'secret',BALE_RECEIVER_TOKEN:'receiver',BALE_SOURCE_CHAT_ID:'4869628883',TELEGRAM_BOT_TOKEN:'telegram',TELEGRAM_CHAT_ID:'@target',BRIDGE_STATE:{get:async k=>sent.get(k),put:async(k,v)=>sent.set(k,v)}}};
const req = body => new Request('https://bridge.example/bale/secret',{method:'POST',body:JSON.stringify(body)});
const update = {update_id:4,message:{chat:{id:4869628883,type:'channel'},text:'TEST'}};
test('auth and source channel',async()=>{
 const e=env(); assert.equal((await worker.fetch(new Request('https://bridge.example/bale/wrong'),e)).status,404);
 assert.equal(await (await worker.fetch(req({...update,message:{...update.message,chat:{id:1,type:'channel'}}}),e)).text(),'Ignored');
});
test('text delivery, dedup and retry after failure',async()=>{
 const old=globalThis.fetch; let calls=0; let fail=true;
 globalThis.fetch=async(url,opts)=>{calls++;assert.equal(opts.redirect,'manual');assert.ok(url.startsWith('https://api.telegram.org/'));assert.equal(opts.body.get('text'),'TEST');return Response.json({ok:!fail,result:{message_id:1}})};
 try {const e=env(); assert.equal((await worker.fetch(req(update),e)).status,502);fail=false;
 assert.equal((await worker.fetch(req(update),e)).status,200);
 assert.equal(await (await worker.fetch(req(update),e)).text(),'Already delivered');assert.equal(calls,2);
 }finally{globalThis.fetch=old;}
});
test('photo downloads from Bale and uploads bytes to Telegram',async()=>{
 const old=globalThis.fetch;let calls=0;
 globalThis.fetch=async(url,opts)=>{calls++; assert.equal(opts.redirect,'manual'); if(url.endsWith('/getFile'))return Response.json({ok:true,result:{file_path:'photos/chart.jpg',file_size:3}});
 if(url.includes('/file/bot'))return new Response(new Uint8Array([1,2,3]));
 assert.ok(url.endsWith('/sendPhoto'));assert.equal(opts.body.get('photo').size,3);assert.equal(opts.body.get('caption'),'chart');return Response.json({ok:true,result:{}});};
 try{const d={update_id:5,message:{chat:update.message.chat,photo:[{file_id:'abc'}],caption:'chart'}};
 assert.equal((await worker.fetch(req(d),env())).status,200);assert.equal(calls,3);
 }finally{globalThis.fetch=old;}
});

test('upstream redirects are rejected without following them',async()=>{
 const old=globalThis.fetch;let calls=0;
 globalThis.fetch=async(url,opts)=>{calls++;assert.equal(opts.redirect,'manual');return new Response(null,{status:302,headers:{Location:'https://other.example'}});};
 try{assert.equal((await worker.fetch(req(update),env())).status,502);assert.equal(calls,1);}
 finally{globalThis.fetch=old;}
});
