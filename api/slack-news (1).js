// Slack /news slash command: answers from the saved radar.json, no AI call.
// Env vars (set on the Vercel project that serves the site):
//   SLACK_SIGNING_SECRET  required. Slack app > Basic Information > Signing Secret
//   SLACK_TEAM_ID         optional. Only allow this workspace (blocks Slack Connect users)
//   SLACK_BOT_TOKEN       optional. If set, guests and bots are rejected (needs users:read)
const crypto = require('crypto');

const RADAR_URL = 'https://raw.githubusercontent.com/jgonzalez-sudo/news-data1/main/radar.json';
const STALE_HOURS = 24;
const MAX_GROUPS_PER_REPLY = 3;

// phrase the user might type -> group name in sources.json
const ALIASES = {
  'business': 'Business', 'markets': 'Business', 'economy': 'Business', 'finance': 'Business', 'top news': 'Business',
  'wires': 'Wires', 'wire': 'Wires',
  'us politics': 'US politics', 'american politics': 'US politics', 'politics': 'US politics',
  'washington': 'US politics', 'congress': 'US politics', 'white house': 'US politics',
  'tech ai': 'Tech & AI', 'ai': 'Tech & AI', 'a i': 'Tech & AI', 'artificial intelligence': 'Tech & AI',
  'tech': 'Tech & AI', 'technology': 'Tech & AI',
  'energy': 'Energy', 'climate': 'Energy', 'oil': 'Energy',
  'china': 'China', 'chinese': 'China', 'beijing': 'China',
  'gulf middle east': 'Gulf & Middle East', 'middle east': 'Gulf & Middle East', 'gulf': 'Gulf & Middle East', 'mideast': 'Gulf & Middle East',
  'africa': 'Africa', 'african': 'Africa',
  'latam': 'LatAm', 'latin america': 'LatAm', 'latin american': 'LatAm', 'south america': 'LatAm',
  'security': 'Security', 'defense': 'Security', 'defence': 'Security', 'military': 'Security',
  'europe ukraine': 'Europe & Ukraine', 'europe': 'Europe & Ukraine', 'european': 'Europe & Ukraine',
  'ukraine': 'Europe & Ukraine', 'ukrainian': 'Europe & Ukraine',
  'asia': 'Asia', 'asian': 'Asia',
  'media': 'Media', 'journalism': 'Media',
};
const ALL_GROUPS = [...new Set(Object.values(ALIASES))];

// ---------- helpers ----------
const norm = (s) => ' ' + String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim() + ' ';

function matchGroups(text) {
  const t = norm(text);
  const hits = [];
  for (const [alias, group] of Object.entries(ALIASES)) {
    if (t.includes(norm(alias)) && !hits.includes(group)) hits.push(group);
  }
  return hits;
}

const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const safeUrl = (u) => String(u).replace(/ /g, '%20').replace(/\|/g, '%7C').replace(/</g, '%3C').replace(/>/g, '%3E');
const clip = (s, n) => (String(s || '').length > n ? String(s).slice(0, n - 1) + '…' : String(s || ''));

function hoursSince(iso) {
  const h = (Date.now() - new Date(iso).getTime()) / 3600000;
  return Number.isFinite(h) ? h : Infinity;
}
function ago(iso) {
  const h = hoursSince(iso);
  if (!Number.isFinite(h)) return 'at an unknown time';
  if (h < 1) return Math.max(1, Math.round(h * 60)) + 'm ago';
  if (h < 48) return Math.round(h) + 'h ago';
  return Math.round(h / 24) + 'd ago';
}

function verifySlack(rawBody, headers, secret) {
  const ts = headers['x-slack-request-timestamp'];
  const sig = headers['x-slack-signature'];
  if (!ts || !sig || !rawBody) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false; // replay protection
  const mine = 'v0=' + crypto.createHmac('sha256', secret).update(`v0:${ts}:${rawBody}`).digest('hex');
  const a = Buffer.from(mine);
  const b = Buffer.from(String(sig));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

async function readRaw(req) {
  if (typeof req.body === 'string' && req.body) return req.body;
  const chunks = [];
  try {
    for await (const c of req) chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c));
  } catch (e) { /* stream already consumed */ }
  const raw = Buffer.concat(chunks).toString('utf8');
  if (raw) return raw;
  // If the platform pre-parsed the form body, rebuild it. The signature check
  // still has to pass, so this can't let a forged request through.
  if (req.body && typeof req.body === 'object') return new URLSearchParams(req.body).toString();
  return '';
}

async function isFullMember(userId, token) {
  const r = await fetch('https://slack.com/api/users.info?user=' + encodeURIComponent(userId), {
    headers: { Authorization: 'Bearer ' + token },
    signal: AbortSignal.timeout(2000),
  });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || 'users.info failed');
  const u = j.user || {};
  return !(u.is_restricted || u.is_ultra_restricted || u.is_bot);
}

// ---------- reply building ----------
const reply = (text, blocks, isPublic) => ({
  response_type: isPublic ? 'in_channel' : 'ephemeral',
  text,
  ...(blocks ? { blocks } : {}),
});

function helpReply(radarGroups) {
  const live = ALL_GROUPS.filter((g) => radarGroups[g]);
  const soon = ALL_GROUPS.filter((g) => !radarGroups[g]);
  const text =
    '*What are our competitors and reference outlets covering?*\n' +
    'Try `/news africa`, `/news china`, `/news energy` or `/news what\'s happening in business?`\n' +
    (live.length ? '*Available now:* ' + live.join(', ') + '\n' : '') +
    (soon.length ? '*Not covered yet:* ' + soon.join(', ') + '\n' : '') +
    'Add the word `public` to post the answer to the channel.';
  return reply('News radar help', [{ type: 'section', text: { type: 'mrkdwn', text } }], false);
}

function groupBlocks(name, g) {
  const blocks = [{ type: 'header', text: { type: 'plain_text', text: clip(name, 150) } }];
  const stale = hoursSince(g.generated_at) > STALE_HOURS;
  blocks.push({
    type: 'context',
    elements: [{
      type: 'mrkdwn',
      text: `${stale ? ':warning: ' : ''}Updated ${ago(g.generated_at)}${stale ? ' (older than 24h)' : ''} · ${esc((g.outlets || []).slice(0, 8).join(', '))}`,
    }],
  });
  if (g.not_visible && g.not_visible.length) {
    blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: ':eyes: Not visible to the radar: ' + esc(g.not_visible.join(', ')) }] });
  }
  if (g.overview) blocks.push({ type: 'section', text: { type: 'mrkdwn', text: clip(esc(g.overview), 2900) } });
  blocks.push({ type: 'divider' });
  for (const s of (g.stories || []).slice(0, 8)) {
    let line = `*<${safeUrl(s.url)}|${esc(clip(s.headline, 200))}>*`;
    if (s.outlet) line += ` · _${esc(s.outlet)}_`;
    if (s.summary) line += `\n${esc(clip(s.summary, 500))}`;
    if (s.also_covered_by && s.also_covered_by.length) line += `\n_Also: ${esc(s.also_covered_by.join(', '))}_`;
    blocks.push({ type: 'section', text: { type: 'mrkdwn', text: line } });
  }
  return blocks;
}

function buildReply(text, radar, isPublic) {
  const groups = (radar && radar.groups) || {};
  const matched = matchGroups(text);
  if (!matched.length) return helpReply(groups);

  const have = matched.filter((g) => groups[g]).slice(0, MAX_GROUPS_PER_REPLY);
  const missing = matched.filter((g) => !groups[g]);
  if (!have.length) {
    return reply(
      `${missing.join(', ')} isn't covered yet.`,
      [{ type: 'section', text: { type: 'mrkdwn', text: `*${esc(missing.join(', '))}* isn't covered by the radar yet. Ask whoever maintains it to switch that group on.` } }],
      false
    );
  }
  let blocks = [];
  have.forEach((g, i) => { if (i) blocks.push({ type: 'divider' }); blocks = blocks.concat(groupBlocks(g, groups[g])); });
  if (missing.length) {
    blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: `Not covered yet: ${esc(missing.join(', '))}` }] });
  }
  blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: 'Headlines and snippets from public sources. Click through with your own subscriptions.' }] });
  return reply('News radar: ' + have.join(', '), blocks, isPublic);
}

// ---------- handler ----------
async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).send('Method not allowed');

  const secret = process.env.SLACK_SIGNING_SECRET;
  if (!secret) return res.status(500).send('SLACK_SIGNING_SECRET is not configured');

  const raw = await readRaw(req);
  if (!verifySlack(raw, req.headers, secret)) return res.status(401).send('Invalid signature');

  const params = new URLSearchParams(raw);
  const expectedTeam = process.env.SLACK_TEAM_ID;
  if (expectedTeam && params.get('team_id') !== expectedTeam) {
    return res.status(200).json(reply('This command is only available to Semafor workspace members.', null, false));
  }

  let text = params.get('text') || '';
  const isPublic = /\bpublic\b/i.test(text);
  text = text.replace(/\bpublic\b/gi, ' ');

  try {
    const botToken = process.env.SLACK_BOT_TOKEN;
    const [radarRes, member] = await Promise.all([
      fetch(RADAR_URL + '?t=' + Date.now(), { signal: AbortSignal.timeout(2000) }),
      botToken ? isFullMember(params.get('user_id'), botToken) : Promise.resolve(true),
    ]);
    if (!member) {
      return res.status(200).json(reply('This command is only available to full Semafor workspace members.', null, false));
    }
    if (!radarRes.ok) throw new Error('radar.json HTTP ' + radarRes.status);
    const radar = await radarRes.json();
    return res.status(200).json(buildReply(text, radar, isPublic));
  } catch (err) {
    console.error('slack-news error:', err && err.message);
    return res.status(200).json(reply("Sorry, I couldn't load the news radar just now. Try again in a minute.", null, false));
  }
}

module.exports = handler;
module.exports.config = { api: { bodyParser: false } }; // we need the raw body to verify Slack's signature
module.exports._internal = { matchGroups, verifySlack, buildReply, helpReply };
