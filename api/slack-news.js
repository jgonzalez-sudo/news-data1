// Slack /news slash command: answers from the saved radar.json, no AI call.
// Env vars (set on the Vercel project that serves the site):
// Names are prefixed NEWS_RADAR_ so they can't clash with other Slack tools on the same Vercel project.
//   NEWS_RADAR_SIGNING_SECRET  required. Slack app > Basic Information > Signing Secret
//   NEWS_RADAR_TEAM_ID         optional. Only allow this workspace (blocks Slack Connect users)
//   NEWS_RADAR_BOT_TOKEN       optional. If set, guests and bots are rejected (needs users:read)
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
  // topical words people use for their beat
  'iran': 'Gulf & Middle East', 'iranian': 'Gulf & Middle East', 'israel': 'Gulf & Middle East', 'gaza': 'Gulf & Middle East',
  'saudi': 'Gulf & Middle East', 'saudi arabia': 'Gulf & Middle East', 'uae': 'Gulf & Middle East', 'emirates': 'Gulf & Middle East',
  'qatar': 'Gulf & Middle East', 'hormuz': 'Gulf & Middle East',
  'taiwan': 'China', 'xi jinping': 'China', 'hong kong': 'China',
  'nvidia': 'Tech & AI', 'openai': 'Tech & AI', 'anthropic': 'Tech & AI', 'chips': 'Tech & AI', 'semiconductors': 'Tech & AI', 'chatgpt': 'Tech & AI',
  'trump': 'US politics', 'senate': 'US politics', 'election': 'US politics', 'elections': 'US politics', 'midterms': 'US politics',
  'supreme court': 'US politics', 'republicans': 'US politics', 'democrats': 'US politics',
  'hollywood': 'Media', 'streaming': 'Media', 'newsroom': 'Media', 'newsrooms': 'Media', 'publishers': 'Media',
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

// Earlier radar runs saved stray <cite index="..."> markers inside some summaries. Drop them before display.
const stripCite = (s) => String(s || '').replace(/<\/?(?:antml:)?cite\b[^>]*>/gi, '');
const esc = (s) => stripCite(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const safeUrl = (u) => String(u).replace(/ /g, '%20').replace(/\|/g, '%7C').replace(/</g, '%3C').replace(/>/g, '%3E');
// Stories saved before types existed have none. Until the next radar run saves real ones, guess from the text.
const TYPE_HINTS = [
  ['Health', /\b(ebola|outbreak|vaccines?|virus|disease|cholera|malaria|mpox|health|hospitals?|epidemic)\b/i],
  ['Security', /\b(attacks?|militants?|terror\w*|insurgen\w*|troops|army|military|gunmen|coup|missiles?|drones?)\b/i],
  ['Geopolitics', /\b(sanctions|summit|treaty|envoy|diplomat\w*|nato|alliance|ceasefire|war)\b/i],
  ['Business', /\b(deal|acquisition|merger|ipo|compan\w*|shares|stock|earnings|profit|investment|startups?|funding|billion|million)\b|[$€£]\s?\d/i],
  ['Energy', /\b(oil|gas|lng|electricity|grid|solar|coal|refiner\w*|pipeline)\b/i],
  ['Economy', /\b(inflation|gdp|central bank|interest rates?|currency|debt|imf|world bank|budget|trade|tariffs?|economy|economic|growth|prices|exports?|imports?)\b/i],
  ['Tech', /\b(ai|artificial intelligence|chips?|software|cyber\w*|data centers?)\b/i],
  ['Politics', /\b(elections?|vote|president|parliament|minister|government|opposition|protests?)\b/i],
];
const inferType = (text) => { for (const [label, re] of TYPE_HINTS) if (re.test(text)) return label; return ''; };
const typeOf = (st) => st.type || inferType(`${st.headline || ''} ${st.summary || ''}`);

// A bold label such as *Health:* in front of each headline, so people scanning can see the kind of story at a glance
const typeLabel = (t) => (t ? `*${esc(clip(t, 24))}:* ` : '');
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

// Slack signs the exact bytes it sent. Different form encoders disagree on a few
// characters (space as + or %20, and whether * ~ ! ' ( ) are escaped), so if we
// only get a parsed body we try each variant. A forged request still can't pass:
// every candidate has to match the HMAC, which needs the signing secret.
function encodeVariant(value, o) {
  let e = encodeURIComponent(String(value));
  if (o.punct) e = e.replace(/[!'()]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());
  if (o.star) e = e.replace(/\*/g, '%2A');
  if (o.tilde) e = e.replace(/~/g, '%7E');
  if (o.plus) e = e.replace(/%20/g, '+');
  return e;
}

function candidateBodies(parsed) {
  const pairs = Object.entries(parsed).flatMap(([k, v]) => (Array.isArray(v) ? v.map((x) => [k, x]) : [[k, v]]));
  const out = new Set();
  for (const plus of [true, false]) for (const star of [false, true]) for (const tilde of [false, true]) for (const punct of [true, false]) {
    const o = { plus, star, tilde, punct };
    out.add(pairs.map(([k, v]) => `${encodeVariant(k, o)}=${encodeVariant(v, o)}`).join('&'));
  }
  return [...out];
}

async function readBody(req) {
  // Read the raw stream BEFORE touching req.body: on some platforms reading
  // req.body parses and consumes the stream, losing the exact bytes.
  const chunks = [];
  try {
    for await (const c of req) chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c));
  } catch (e) { /* stream already consumed */ }
  const raw = Buffer.concat(chunks).toString('utf8');
  if (raw) return { candidates: [raw], source: 'stream' };
  const b = req.body;
  if (typeof b === 'string' && b) return { candidates: [b], source: 'body-string' };
  if (Buffer.isBuffer(b) && b.length) return { candidates: [b.toString('utf8')], source: 'body-buffer' };
  if (b && typeof b === 'object') return { candidates: candidateBodies(b), source: 'body-parsed' };
  return { candidates: [], source: 'empty' };
}

function verifyCandidates(candidates, headers, secret) {
  for (const c of candidates) if (verifySlack(c, headers, secret)) return c;
  return null;
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

function helpReply(radarGroups, newsletters) {
  const live = ALL_GROUPS.filter((g) => radarGroups[g]);
  const soon = ALL_GROUPS.filter((g) => !radarGroups[g]);
  const picks = Object.keys(newsletters || {});
  const text =
    '*What are our competitors and reference outlets covering?*\n' +
    'Try `/news africa`, `/news china`, `/news energy` or `/news what\'s happening in business?`\n' +
    (picks.length ? 'Want story ideas for a newsletter? Try `/news what should Flagship consider today?` or `/news what do you recommend for the Energy newsletter?`\n' : '') +
    (live.length ? '*Beats available now:* ' + live.join(', ') + '\n' : '') +
    (picks.length ? '*Newsletter ideas available:* ' + picks.join(', ') + '\n' : '') +
    (soon.length ? '*Beats not saved yet:* ' + soon.join(', ') + '\n' : '') +
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
    let line = `${typeLabel(typeOf(s))}<${safeUrl(s.url)}|${esc(clip(s.headline, 200))}>`;
    if (s.outlet) line += ` · _${esc(s.outlet)}_`;
    if (s.summary) line += `\n${esc(clip(s.summary, 500))}`;
    if (s.also_covered_by && s.also_covered_by.length) line += `\n_Also: ${esc(s.also_covered_by.join(', '))}_`;
    blocks.push({ type: 'section', text: { type: 'mrkdwn', text: line } });
  }
  return blocks;
}

// Newsletter ideas: "what should Flagship consider today?", "what do you recommend for the Energy newsletter?"
const NEWSLETTER_ALIASES = {
  'flagship': 'Flagship', 'africa': 'Africa', 'energy': 'Energy', 'net zero': 'Energy',
  'tech': 'Tech', 'technology': 'Tech', 'ai': 'Tech', 'business': 'Business', 'gulf': 'Gulf', 'china': 'China',
  'politics': 'Politics', 'principals': 'Politics', 'americana': 'Politics', 'media': 'Media',
};
const RECOMMEND_INTENT = /\b(recommend\w*|consider|should|pitch\w*|picks?|newsletter|ideas?|suggest\w*)\b/i;

function matchNewsletter(text) {
  const t = norm(text);
  if (t.includes(' flagship ')) return 'Flagship';           // Flagship isn't a beat, so any mention means picks
  if (!RECOMMEND_INTENT.test(text)) return null;              // otherwise "energy" alone still means the beat summary
  for (const [alias, name] of Object.entries(NEWSLETTER_ALIASES)) {
    if (t.includes(norm(alias))) return name;
  }
  return null;
}

function picksBlocks(name, n) {
  const blocks = [{ type: 'header', text: { type: 'plain_text', text: clip(`${name}: what to consider today`, 150) } }];
  const stale = hoursSince(n.generated_at) > STALE_HOURS;
  const basis = n.confidence === 'draft' ? 'draft guide, based on limited examples' : "matched to recent Semafor issues";
  blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: `${stale ? ':warning: ' : ''}Updated ${ago(n.generated_at)}${stale ? ' (older than 24h)' : ''} · ${basis}` }] });
  if (n.intro) blocks.push({ type: 'section', text: { type: 'mrkdwn', text: clip(esc(n.intro), 2900) } });
  blocks.push({ type: 'divider' });
  for (const p of (n.picks || []).slice(0, 7)) {
    let line = `${typeLabel(typeOf(p))}<${safeUrl(p.url)}|${esc(clip(p.headline, 200))}>`;
    if (p.outlet) line += ` · _${esc(p.outlet)}_`;
    if (p.group) line += ` (${esc(p.group)})`;
    if (p.why) line += `\n${esc(clip(p.why, 300))}`;
    blocks.push({ type: 'section', text: { type: 'mrkdwn', text: line } });
  }
  blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: 'Ideas picked from what other outlets are running, matched to the newsletter\'s habits. Suggestions, not assignments.' }] });
  return blocks;
}

function buildReply(text, radar, isPublic) {
  const groups = (radar && radar.groups) || {};
  const newsletters = (radar && radar.newsletters) || {};

  const nl = matchNewsletter(text);
  if (nl) {
    const entry = newsletters[nl];
    if (entry && entry.picks && entry.picks.length) return reply(`${nl}: what to consider today`, picksBlocks(nl, entry), isPublic);
    return reply(`No ideas saved yet for ${nl}.`,
      [{ type: 'section', text: { type: 'mrkdwn', text: `There are no saved story ideas for the *${esc(nl)}* newsletter yet. They are created with each radar run, so try again after the next one.` } }], false);
  }

  const matched = matchGroups(text);
  if (!matched.length) return helpReply(groups, newsletters);

  const have = matched.filter((g) => groups[g]).slice(0, MAX_GROUPS_PER_REPLY);
  const missing = matched.filter((g) => !groups[g]);
  if (!have.length) {
    return reply(
      `${missing.join(', ')} has no saved results yet.`,
      [{ type: 'section', text: { type: 'mrkdwn', text: `*${esc(missing.join(', '))}* has no saved results yet. Either that beat is switched off, or its latest run didn't finish. Try again after the next radar run.` } }],
      false
    );
  }
  let blocks = [];
  have.forEach((g, i) => { if (i) blocks.push({ type: 'divider' }); blocks = blocks.concat(groupBlocks(g, groups[g])); });
  if (missing.length) {
    blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: `No saved results yet: ${esc(missing.join(', '))}` }] });
  }
  blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: 'Headlines and snippets from public sources. Click through with your own subscriptions.' }] });
  return reply('News radar: ' + have.join(', '), blocks, isPublic);
}

// ---------- handler ----------
const STARTED_AT = new Date().toISOString();
const RECENT = [];              // last few /news attempts seen by THIS server instance (shown at ?debug=1)
const SLACK_DEADLINE_MS = 2600; // Slack gives up at 3s, so answer with something readable before then

async function statusPage(req, res) {
  const info = {
    ok: true,
    signing_secret_configured: Boolean(process.env.NEWS_RADAR_SIGNING_SECRET),
    team_id_configured: Boolean(process.env.NEWS_RADAR_TEAM_ID),
    bot_token_configured: Boolean(process.env.NEWS_RADAR_BOT_TOKEN),
    radar: null,
  };
  try {
    const r = await fetch(RADAR_URL + '?t=' + Date.now(), { signal: AbortSignal.timeout(2500) });
    const j = r.ok ? await r.json() : {};
    info.radar = { reachable: r.ok, generated_at: j.generated_at || null, groups: Object.keys(j.groups || {}) };
  } catch (e) {
    info.radar = { reachable: false, error: String(e && e.message) };
  }
  let debug = false;
  try { debug = new URL(req.url || '', 'http://x').searchParams.get('debug') === '1'; } catch (e) { /* no url */ }
  if (debug) {
    info.instance_started_at = STARTED_AT;
    info.recent_attempts = RECENT;
    info.note = 'Each server instance keeps its own list. If it is empty right after you ran /news, the request did not reach this instance.';
  }
  res.setHeader('Cache-Control', 'no-store');
  return res.status(200).json(info);
}

async function handle(req, attempt) {
  const secret = process.env.NEWS_RADAR_SIGNING_SECRET;
  if (!secret) {
    attempt.outcome = 'signing secret missing';
    console.error('slack-news: NEWS_RADAR_SIGNING_SECRET is not set on the Vercel project that serves this address');
    return { status: 500, text: 'NEWS_RADAR_SIGNING_SECRET is not configured' };
  }

  attempt.stage = 'reading-body';
  const { candidates, source } = await readBody(req);
  attempt.body_source = source;

  attempt.stage = 'verifying-signature';
  const ts = req.headers['x-slack-request-timestamp'];
  if (!ts || !req.headers['x-slack-signature']) {
    attempt.outcome = 'no Slack signature headers';
    console.error('slack-news: request has no Slack signature headers');
    return { status: 401, text: 'Invalid signature' };
  }
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) {
    attempt.outcome = 'timestamp more than 5 minutes off';
    console.error('slack-news: request timestamp is more than 5 minutes off');
    return { status: 401, text: 'Invalid signature' };
  }
  const raw = verifyCandidates(candidates, req.headers, secret);
  if (!raw) {
    attempt.outcome = `signature did not match (${candidates.length} candidate(s) tried)`;
    console.error(`slack-news: signature did not match (body source: ${source}, ${candidates.length} candidate(s)). Check NEWS_RADAR_SIGNING_SECRET is the one from THIS Slack app.`);
    return { status: 401, text: 'Invalid signature' };
  }
  console.log(`slack-news: verified (body source: ${source})`);

  const params = new URLSearchParams(raw);
  const expectedTeam = process.env.NEWS_RADAR_TEAM_ID;
  if (expectedTeam && params.get('team_id') !== expectedTeam) {
    attempt.outcome = 'wrong workspace';
    return { status: 200, json: reply('This command is only available to Semafor workspace members.', null, false) };
  }

  let text = params.get('text') || '';
  const isPublic = /\bpublic\b/i.test(text);
  text = text.replace(/\bpublic\b/gi, ' ');

  attempt.stage = 'loading-radar';
  try {
    const botToken = process.env.NEWS_RADAR_BOT_TOKEN;
    const [radarRes, member] = await Promise.all([
      fetch(RADAR_URL + '?t=' + Date.now(), { signal: AbortSignal.timeout(2000) }),
      botToken ? isFullMember(params.get('user_id'), botToken) : Promise.resolve(true),
    ]);
    if (!member) {
      attempt.outcome = 'guest or bot refused';
      return { status: 200, json: reply('This command is only available to full Semafor workspace members.', null, false) };
    }
    if (!radarRes.ok) throw new Error('radar.json HTTP ' + radarRes.status);
    const radar = await radarRes.json();
    attempt.outcome = 'answered';
    return { status: 200, json: buildReply(text, radar, isPublic) };
  } catch (err) {
    attempt.outcome = 'radar or membership check failed: ' + (err && err.message);
    console.error('slack-news error:', err && err.message);
    return { status: 200, json: reply("Sorry, I couldn't load the news radar just now. Try again in a minute.", null, false) };
  }
}

async function handler(req, res) {
  if (req.method === 'GET') return statusPage(req, res);
  if (req.method !== 'POST') return res.status(405).send('Method not allowed');

  const attempt = {
    at: new Date().toISOString(), stage: 'start', outcome: 'pending',
    content_type: req.headers['content-type'] || null,
    content_length: req.headers['content-length'] || null,
    has_signature: Boolean(req.headers['x-slack-signature']),
  };
  RECENT.unshift(attempt);
  if (RECENT.length > 10) RECENT.length = 10;

  const t0 = Date.now();
  let timer;
  const deadline = new Promise((resolve) => {
    timer = setTimeout(() => resolve({
      status: 200, timedOut: true,
      json: reply(`The News Radar got stuck at the step "${attempt.stage}". Please tell whoever built this app.`, null, false),
    }), SLACK_DEADLINE_MS);
  });

  let result;
  try {
    result = await Promise.race([handle(req, attempt), deadline]);
  } catch (err) {
    attempt.outcome = 'crashed: ' + (err && err.message);
    console.error('slack-news crashed:', err && err.stack);
    result = { status: 200, json: reply(`Sorry, something went wrong on my side (step "${attempt.stage}").`, null, false) };
  }
  clearTimeout(timer);
  if (result.timedOut) {
    attempt.outcome = `timed out at step ${attempt.stage}`;
    console.error(`slack-news: timed out at step "${attempt.stage}"`);
  } else {
    attempt.stage = 'done';
  }
  attempt.status = result.status;
  attempt.ms = Date.now() - t0;
  return result.json ? res.status(result.status).json(result.json) : res.status(result.status).send(result.text);
}

module.exports = handler;
module.exports.config = { api: { bodyParser: false } }; // we need the raw body to verify Slack's signature
module.exports._internal = { matchNewsletter, matchGroups, verifySlack, verifyCandidates, buildReply, helpReply, candidateBodies, readBody, isFullMember, RADAR_URL };
