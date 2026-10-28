// Slack chat mode: people can DM the News Radar app, or @mention it in a channel,
// and it answers from the saved radar.json, exactly like the /news command does.
// Env vars (same Vercel project as slack-news.js):
//   NEWS_RADAR_SIGNING_SECRET  required
//   NEWS_RADAR_BOT_TOKEN       required. The "Bot User OAuth Token" (xoxb-...), needs chat:write
//   NEWS_RADAR_TEAM_ID         optional. Only answer this workspace
const crypto = require('crypto');
const news = require('./slack-news')._internal;

// Slack's JSON can be escaped slightly differently from JSON.stringify, so if we only
// get a parsed body we try a few spellings. A forged request still fails the HMAC.
function jsonCandidates(obj) {
  const compact = JSON.stringify(obj);
  const escaped = compact.replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
  return [...new Set([compact, escaped])];
}

async function readEventBody(req) {
  // stream first: touching req.body can consume it on some platforms
  const chunks = [];
  try {
    for await (const c of req) chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c));
  } catch (e) { /* already consumed */ }
  const raw = Buffer.concat(chunks).toString('utf8');
  if (raw) return { candidates: [raw], source: 'stream' };
  const b = req.body;
  if (typeof b === 'string' && b) return { candidates: [b], source: 'body-string' };
  if (Buffer.isBuffer(b) && b.length) return { candidates: [b.toString('utf8')], source: 'body-buffer' };
  if (b && typeof b === 'object') return { candidates: jsonCandidates(b), source: 'body-parsed' };
  return { candidates: [], source: 'empty' };
}

async function postMessage(token, ev, message) {
  const payload = { channel: ev.channel, text: message.text };
  if (message.blocks) payload.blocks = message.blocks;
  if (ev.type === 'app_mention') payload.thread_ts = ev.thread_ts || ev.ts; // keep channel replies in a thread
  const r = await fetch('https://slack.com/api/chat.postMessage', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8', Authorization: 'Bearer ' + token },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(2500),
  });
  const j = await r.json();
  if (!j.ok) throw new Error('chat.postMessage failed: ' + j.error);
}

async function handler(req, res) {
  if (req.method === 'GET') {
    return res.status(200).json({
      ok: true,
      endpoint: 'slack-events',
      signing_secret_configured: Boolean(process.env.NEWS_RADAR_SIGNING_SECRET),
      bot_token_configured: Boolean(process.env.NEWS_RADAR_BOT_TOKEN),
    });
  }
  if (req.method !== 'POST') return res.status(405).send('Method not allowed');

  const secret = process.env.NEWS_RADAR_SIGNING_SECRET;
  if (!secret) {
    console.error('slack-events: NEWS_RADAR_SIGNING_SECRET is not set on this Vercel project');
    return res.status(500).send('NEWS_RADAR_SIGNING_SECRET is not configured');
  }

  const { candidates, source } = await readEventBody(req);
  const raw = news.verifyCandidates(candidates, req.headers, secret);
  if (!raw) {
    console.error(`slack-events: signature did not match (body source: ${source}, ${candidates.length} candidate(s))`);
    return res.status(401).send('Invalid signature');
  }

  let body;
  try { body = JSON.parse(raw); } catch (e) { return res.status(400).send('Bad JSON'); }

  // Slack asks us to prove we own this address when the URL is first saved
  if (body.type === 'url_verification') return res.status(200).json({ challenge: body.challenge });

  // If we were too slow, Slack re-sends the event. We already handled it, so ignore retries.
  if (req.headers['x-slack-retry-num']) return res.status(200).send('ok');

  const expectedTeam = process.env.NEWS_RADAR_TEAM_ID;
  if (expectedTeam && body.team_id !== expectedTeam) return res.status(200).send('ok');
  if (body.type !== 'event_callback') return res.status(200).send('ok');

  const ev = body.event || {};
  let text = null;
  if (ev.type === 'message' && ev.channel_type === 'im' && !ev.subtype && !ev.bot_id && ev.user && typeof ev.text === 'string') {
    text = ev.text;
  } else if (ev.type === 'app_mention' && ev.user && !ev.bot_id) {
    text = String(ev.text || '').replace(/<@[A-Z0-9]+>/g, ' ');
  }
  if (text === null) return res.status(200).send('ok'); // not something we answer (our own messages, edits, other channels)

  const token = process.env.NEWS_RADAR_BOT_TOKEN;
  if (!token) {
    console.error('slack-events: NEWS_RADAR_BOT_TOKEN is not set, so I cannot reply');
    return res.status(200).send('ok');
  }

  try {
    const [radarRes, member] = await Promise.all([
      fetch(news.RADAR_URL + '?t=' + Date.now(), { signal: AbortSignal.timeout(2000) }),
      news.isFullMember(ev.user, token),
    ]);
    let message;
    if (!member) {
      message = { text: 'This is only available to full Semafor workspace members.' };
    } else {
      if (!radarRes.ok) throw new Error('radar.json HTTP ' + radarRes.status);
      message = news.buildReply(text, await radarRes.json(), false);
    }
    await postMessage(token, ev, message);
    console.log(`slack-events: answered (${ev.type}, body source: ${source})`);
  } catch (err) {
    console.error('slack-events error:', err && err.message);
    try {
      await postMessage(token, ev, { text: "Sorry, I couldn't load the news radar just now. Try again in a minute." });
    } catch (e2) { console.error('slack-events: could not post the error message either:', e2 && e2.message); }
  }
  return res.status(200).send('ok');
}

module.exports = handler;
module.exports.config = { api: { bodyParser: false } }; // we need the exact bytes to verify Slack's signature
module.exports._internal = { jsonCandidates, readEventBody };
