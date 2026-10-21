import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../cloudflare/telegram-relay/worker.mjs';
const env={TELEGRAM_BOT_TOKEN:'123456:fake_token', RELAY_KEY:'a'.repeat(40)};
const req=(path,extra={})=>new Request('https://relay.example'+path,{method:'POST',headers:{Authorization:'Bearer '+env.RELAY_KEY,'Content-Type':'application/x-www-form-urlencoded'},body:'timeout=0',...extra});
test('unauthenticated callers are rejected',async()=>{
 const r=await worker.fetch(req('/telegram/getUpdates',{headers:{}}),env);assert.equal(r.status,401);
});
test('arbitrary Telegram methods and URLs rejected',async()=>{
 for(const path of ['/telegram/deleteWebhook','/telegram/getUpdates?url=https://evil.example']) {
  assert.equal((await worker.fetch(req(path),env)).status,404);
 }
});
test('upstream fixed host/token and client authorization not forwarded',async()=>{
 const saved=globalThis.fetch;
 try {
  globalThis.fetch=async(url,options)=>{assert.equal(url,'https://api.telegram.org/bot123456:fake_token/getUpdates');assert.equal(options.headers.Authorization,undefined);assert.equal(new TextDecoder().decode(options.body),'timeout=0');return Response.json({ok:true,result:[]});};
  assert.deepEqual(await (await worker.fetch(req('/telegram/getUpdates'),env)).json(),{ok:true,result:[]});
 } finally {globalThis.fetch=saved;}
});
test('upstream exceptions never expose secrets',async()=>{
 const saved=globalThis.fetch;
 try {globalThis.fetch=async()=>{throw Error(env.TELEGRAM_BOT_TOKEN);};
 const r=await worker.fetch(req('/telegram/getMe'),env);assert.equal(r.status,502);assert.equal((await r.text()).includes(env.TELEGRAM_BOT_TOKEN),false);
 } finally {globalThis.fetch=saved;}
});
test('health contains no secrets and does not claim Telegram verified',async()=>{
 const d=await (await worker.fetch(new Request('https://relay.example/health'),{})).json();assert.deepEqual(d,{ok:true,service:'watchbot-telegram-relay'});
});
