// One-way bridge. Never log request URLs, bodies or tokens.
const MAX = 9_000_000;
async function api(base, method, body) {
  const r = await fetch(`${base}/${method}`, {method: 'POST', body,
    redirect: 'manual', signal: AbortSignal.timeout(20000)});
  if (r.status >= 300 && r.status < 400) throw new Error('Redirect rejected');
  const d = await r.json();
  if (!r.ok || !d.ok) throw new Error('Upstream request failed');
  return d.result;
}
async function bounded(response, limit = MAX) {
  if (response instanceof Response && !response.ok) throw new Error('Download failed');
  const reader = response.body.getReader(); const chunks = []; let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      size += value.length;
      if (size > limit) throw new Error('Photo too large');
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  return new Blob(chunks, {type: 'image/jpeg'});
}
export default {
  async fetch(request, env) {
    const reply = (status, text) => new Response(text, {status});
    if (new URL(request.url).pathname === '/health') return reply(200, 'bridge');
    if (!env.WEBHOOK_KEY || !env.BALE_RECEIVER_TOKEN || !env.BALE_SOURCE_CHAT_ID ||
        !env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID || !env.BRIDGE_STATE)
      return reply(503, 'Configuration incomplete');
    const path = new URL(request.url).pathname;
    if (request.method !== 'POST' || path !== '/bale/' + env.WEBHOOK_KEY)
      return reply(404, 'Not found');
    try {
      // Bound streamed input, including requests without Content-Length.
      const raw = await bounded(request, 100000);
      if (raw.size > 100000) return reply(413, 'Update too large');
      const update = JSON.parse(await raw.text());
      const msg = update.message || update.channel_post;
      if (!msg || msg.chat?.type !== 'channel' ||
          String(msg.chat.id) !== String(env.BALE_SOURCE_CHAT_ID)) return reply(200, 'Ignored');
      if (!Number.isSafeInteger(update.update_id)) return reply(400, 'Invalid update');
      if (!msg.text && !msg.photo?.length) return reply(200, 'Ignored');
      const key = `update:${env.BALE_SOURCE_CHAT_ID}:${update.update_id}`;
      if (await env.BRIDGE_STATE.get(key)) return reply(200, 'Already delivered');
      const bale = `https://tapi.bale.ai/bot${env.BALE_RECEIVER_TOKEN}`;
      const telegram = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}`;
      if (msg.photo?.length) {
        const id = msg.photo.at(-1).file_id;
        const file = await api(bale, 'getFile', new URLSearchParams({file_id: id}));
        if (file.file_size > MAX || typeof file.file_path !== 'string' ||
            !/^[A-Za-z0-9_./-]+$/.test(file.file_path) ||
            file.file_path.startsWith('/') || file.file_path.split('/').includes('..'))
          throw new Error('Invalid file');
        const image = await bounded(await fetch(
          `https://tapi.bale.ai/file/bot${env.BALE_RECEIVER_TOKEN}/${file.file_path}`,
          {redirect: 'manual', signal: AbortSignal.timeout(20000)}));
        const form = new FormData(); form.set('chat_id', env.TELEGRAM_CHAT_ID);
        form.set('caption', (msg.caption || '').slice(0, 1000));
        form.set('photo', image, 'analysis.jpg');
        await api(telegram, 'sendPhoto', form);
      } else {
        await api(telegram, 'sendMessage', new URLSearchParams({
          chat_id: env.TELEGRAM_CHAT_ID, text: msg.text.slice(0, 4000)}));
      }
      // KV suppresses subsequent duplicates; concurrent delivery or a lost response
      // after Telegram accepts may still duplicate. This is not exactly-once.
      await env.BRIDGE_STATE.put(key, 'sent', {expirationTtl: 172800});
      return reply(200, 'Delivered');
    } catch { return reply(502, 'Delivery failed'); }
  }
};
