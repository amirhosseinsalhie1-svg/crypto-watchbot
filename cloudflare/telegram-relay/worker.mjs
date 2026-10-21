// Deploy as its own Worker. Secrets: TELEGRAM_BOT_TOKEN, RELAY_KEY.
const METHODS = new Set(['getMe', 'getUpdates', 'sendMessage', 'sendPhoto']);
const MAX_BODY = 9500000;
const reply = (status, body) => Response.json(body, {status, headers: {'Cache-Control': 'no-store'}});
async function equalSecret(a, b) {
  const digest = async s => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
  const [x, y] = await Promise.all([digest(a), digest(b)]);
  let different = 0;
  for (let i = 0; i < x.length; i++) different |= x[i] ^ y[i];
  return different === 0;
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/health' && request.method === 'GET') {
      return reply(200, {ok: true, service: 'watchbot-telegram-relay'});
    }
    if (!env.RELAY_KEY || env.RELAY_KEY.length < 32 || !/^\d+:[A-Za-z0-9_-]+$/.test(env.TELEGRAM_BOT_TOKEN || '')) {
      return reply(503, {ok: false, description: 'Relay secrets not configured'});
    }
    const authorization = request.headers.get('Authorization') || '';
    if (!await equalSecret(authorization, 'Bearer ' + env.RELAY_KEY)) {
      return reply(401, {ok: false, description: 'Unauthorized'});
    }
    const method = url.pathname.slice('/telegram/'.length);
    if (!url.pathname.startsWith('/telegram/') || !METHODS.has(method) || url.search) {
      return reply(404, {ok: false, description: 'Unsupported relay endpoint'});
    }
    if (request.method !== 'POST') return reply(405, {ok: false, description: 'POST required'});
    const contentType = request.headers.get('Content-Type') || '';
    if (!contentType.startsWith('application/x-www-form-urlencoded') && !contentType.startsWith('multipart/form-data;')) {
      return reply(415, {ok: false, description: 'Unsupported body format'});
    }
    if (Number(request.headers.get('Content-Length') || 0) > MAX_BODY) return reply(413, {ok: false});
    try {
      const chunks = []; let total = 0;
      const reader = request.body?.getReader();
      if (reader) {
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          total += value.byteLength;
          if (total > MAX_BODY) { await reader.cancel(); return reply(413, {ok: false}); }
          chunks.push(value);
        }
      }
      const bytes = new Uint8Array(total); let position = 0;
      for (const chunk of chunks) { bytes.set(chunk, position); position += chunk.length; }
      // Fixed Telegram host and server-side bot token; never forward client headers or URLs.
      const upstream = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
        method: 'POST', headers: {'Content-Type': contentType}, body: bytes,
        redirect: 'manual', signal: AbortSignal.timeout(15000)
      });
      if (upstream.status >= 300 && upstream.status < 400) {
        return reply(502, {ok: false, description: 'Upstream redirect rejected'});
      }
      const data = await upstream.json();
      if (!data.ok) {
        return reply(upstream.status >= 400 ? upstream.status : 502,
          {ok: false, error_code: data.error_code, description: 'Telegram rejected request'});
      }
      return reply(200, data);
    } catch {
      return reply(502, {ok: false, description: 'Telegram upstream unavailable'});
    }
  }
};
