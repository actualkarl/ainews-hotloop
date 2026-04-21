// ainews.hotloop.ai — Worker entry.
// Routes /api/refresh to a Telegram ping; everything else falls through to
// the static asset binding (public/).

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === '/api/refresh' && request.method === 'POST') {
      return handleRefresh(env);
    }

    return env.ASSETS.fetch(request);
  },
};

async function handleRefresh(env) {
  const token = env.TELEGRAM_BOT_TOKEN;
  const chatId = env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) {
    return new Response(
      JSON.stringify({ ok: false, error: 'TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured' }),
      { status: 503, headers: { 'content-type': 'application/json' } }
    );
  }

  const text = '🔄 ainews refresh requested from the site — run `/routine run ainews` on the Mac mini when ready.';
  const tgRes = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, text }),
  });

  if (!tgRes.ok) {
    return new Response(
      JSON.stringify({ ok: false, error: `telegram ${tgRes.status}` }),
      { status: 502, headers: { 'content-type': 'application/json' } }
    );
  }

  return new Response(
    JSON.stringify({ ok: true }),
    { status: 200, headers: { 'content-type': 'application/json' } }
  );
}
