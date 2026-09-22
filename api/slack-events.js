// /api/slack-events.js
//
// Lets people DM the Slack app directly (e.g. "who can help me build a chart")
// instead of typing a slash command. Same matching logic as /api/whoshelp,
// just triggered by a direct message instead of "/help ...".
//
// Fully self-contained — does NOT depend on any other file in this repo.
//
// ---- one-time setup (in addition to what /help already needed) ----
// 1. Deploy this file to news-data1 at api/slack-events.js.
// 2. In the Slack app settings, left sidebar -> "OAuth & Permissions":
//    - Under "Scopes" -> "Bot Token Scopes", add: chat:write, im:history
//    - Click "Reinstall to Semafor". Copy the new Bot User OAuth Token
//      (starts with xoxb-) and add it to Vercel as SLACK_BOT_TOKEN, redeploy.
// 3. Left sidebar -> "App Home": turn on "Messages Tab" + check
//    "Allow users to send Slash commands and messages from the messages tab".
// 4. Left sidebar -> "Event Subscriptions": turn Events on. Request URL:
//      https://news-data1.vercel.app/api/slack-events
// 5. Under "Subscribe to bot events", add: message.im. Save + reinstall.
// 6. Uses the ANTHROPIC_API_KEY already set in this Vercel project as a
//    fallback for queries that don't directly match anyone's "go to for" text.

export const config = {
  api: { bodyParser: false },
};

const WORK_START_HOUR = 8;
const WORK_END_HOUR = 19;
const CLAUDE_MODEL = 'claude-haiku-4-5-20251001';

const SHEET_CSV_URL = process.env.SHEET_CSV_URL ||
  'https://docs.google.com/spreadsheets/d/e/2PACX-1vRrtWiQmcJfaEE0W1NAyauZNLiDBttCP2Jk9jJGLJP0_K_YNcCAM-3zqUZWvZkzqFXSKHND3e_fP9pW/pub?gid=1852591954&single=true&output=csv';

const STOPWORDS = new Set(['who', 'can', 'help', 'me', 'i', 'a', 'an', 'the', 'with',
  'for', 'to', 'now', 'today', 'on', 'is', 'are', 'do', 'does', 'need', 'needs',
  'my', 'our', 'we', 'us', 'please', 'and', 'or', 'of', 'in', 'get', 'got']);

const DESIGN_KEYWORDS = ['chart', 'graphic', 'figma', 'design', 'visual',
  'mockup', 'mock-up', 'illustration', 'infographic', 'dataviz', 'viz', 'diagram'];

function isDesignQuery(query) {
  const q = query.toLowerCase();
  return DESIGN_KEYWORDS.some((k) => q.includes(k));
}

function fellowsMatch(people) {
  return people
    .filter((p) => /fellow/i.test(p.title))
    .map((p) => ({ name: p.name, reason: 'Fellow — go-to for charts/Figma/design work' }));
}

function readRawBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => (data += chunk));
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

async function verifySlackSignature(req, rawBody) {
  const secret = process.env.SLACK_SIGNING_SECRET;
  if (!secret) return true;

  const crypto = await import('crypto');
  const timestamp = req.headers['x-slack-request-timestamp'];
  const slackSig = req.headers['x-slack-signature'];
  if (!timestamp || !slackSig) return false;
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 60 * 5) return false;

  const baseString = `v0:${timestamp}:${rawBody}`;
  const hmac = crypto.createHmac('sha256', secret).update(baseString).digest('hex');
  const computedSig = `v0=${hmac}`;
  if (computedSig.length !== slackSig.length) return false;
  return crypto.timingSafeEqual(Buffer.from(computedSig), Buffer.from(slackSig));
}

function parseCSV(text) {
  const rows = [];
  let field = '';
  let row = [];
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(field); field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else {
      field += c;
    }
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows;
}

function parseRoster(csvText) {
  const rows = parseCSV(csvText);
  const headers = rows[0].map((h) => h.trim());
  return rows.slice(1).map((r) => {
    const obj = {};
    headers.forEach((h, i) => (obj[h] = (r[i] || '').trim()));
    return {
      name: obj.name || '',
      title: obj.title || '',
      teams: (obj.teams || '').split(',').map((s) => s.trim()).filter(Boolean),
      location: obj.location || '',
      go_to_for: obj.go_to_for || '',
      bio: obj.bio || '',
      photo_url: obj.photo_url || '',
    };
  }).filter((p) => p.name);
}

function parseEtOffset(location) {
  const m = /ET\s*([+-]\d+)?/.exec(location || '');
  if (!m) return null;
  return m[1] ? parseInt(m[1], 10) : 0;
}

function nowEtParts() {
  const etString = new Date().toLocaleString('en-US', { timeZone: 'America/New_York' });
  return new Date(etString);
}

function localTimeFor(offset) {
  const etNow = nowEtParts();
  return new Date(etNow.getTime() + offset * 60 * 60 * 1000);
}

function isWorkingHours(localDate) {
  const day = localDate.getDay();
  const hour = localDate.getHours();
  return day >= 1 && day <= 5 && hour >= WORK_START_HOUR && hour < WORK_END_HOUR;
}

function fmtTime(d) {
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function personLine(a) {
  return `*${a.name}* — ${a.location || 'location unknown'}${a.localTime ? ` — ${a.localTime}` : ''}`;
}

function personBlock(a, byName) {
  const person = byName[a.name];
  const block = {
    type: 'section',
    text: { type: 'mrkdwn', text: personLine(a) },
  };
  if (person && person.photo_url) {
    block.accessory = { type: 'image', image_url: person.photo_url, alt_text: a.name };
  }
  return block;
}

function buildResponse(query, annotated, byName) {
  const etNow = fmtTime(nowEtParts());
  const available = annotated.filter((a) => a.available === true);
  const others = annotated.filter((a) => a.available !== true);

  const blocks = [
    { type: 'section', text: { type: 'mrkdwn', text: `*Who can help — "${query}"*  _(as of ${etNow} ET)_` } },
  ];

  if (available.length) {
    blocks.push({ type: 'section', text: { type: 'mrkdwn', text: '*🟢 Available now:*' } });
    available.forEach((a) => blocks.push(personBlock(a, byName)));
  }
  if (others.length) {
    blocks.push({ type: 'divider' });
    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: available.length ? '*Also can help (outside typical hours or unknown location):*' : '*Can help:*' },
    });
    others.forEach((a) => blocks.push(personBlock(a, byName)));
  }

  const fallbackText = `Who can help — "${query}" (as of ${etNow} ET): ` +
    [...available, ...others].map((a) => a.name).join(', ');

  return { text: fallbackText, blocks };
}

function tokenize(text) {
  return (text.toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => w.length > 2 && !STOPWORDS.has(w));
}

function keywordMatch(query, people) {
  const tokens = tokenize(query);
  if (!tokens.length) return [];
  const scored = people.map((p) => {
    const haystack = `${p.go_to_for} ${p.teams.join(' ')} ${p.bio}`.toLowerCase();
    const score = tokens.reduce((s, t) => s + (haystack.includes(t) ? 1 : 0), 0);
    return { p, score };
  }).filter((x) => x.score > 0);
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, 5).map((x) => ({ name: x.p.name, reason: x.p.go_to_for || x.p.bio || '' }));
}

async function claudeMatch(query, people) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return [];

  const rosterText = people
    .map((p) => `${p.name} | Teams: ${p.teams.join(', ')} | Go to for: ${p.go_to_for || p.bio || 'n/a'}`)
    .join('\n');

  const systemPrompt = `You are a lookup assistant for Semafor's internal staff directory. ` +
    `A colleague will ask a plain-English question about who can help with something. ` +
    `Using ONLY the roster below, pick up to 5 people who could plausibly help. ` +
    `Respond with ONLY a JSON array like [{"name":"Full Name","reason":"short reason"}] and nothing else. ` +
    `If truly nobody fits, respond with [].\n\nRoster:\n${rosterText}`;

  const resp = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: CLAUDE_MODEL,
      max_tokens: 500,
      system: systemPrompt,
      messages: [{ role: 'user', content: query }],
    }),
  });
  if (!resp.ok) throw new Error(`Claude API failed: ${resp.status}`);
  const data = await resp.json();
  const textBlock = (data.content || []).find((b) => b.type === 'text');
  if (!textBlock) return [];
  try {
    const cleaned = textBlock.text.replace(/```json|```/g, '').trim();
    return JSON.parse(cleaned);
  } catch {
    return [];
  }
}

async function findMatches(query, people) {
  const isDesign = isDesignQuery(query);
  const fellows = isDesign ? fellowsMatch(people) : [];
  const direct = keywordMatch(query, people);

  let fromClaude = [];
  try {
    fromClaude = await claudeMatch(query, people);
  } catch {
    fromClaude = [];
  }

  const seen = new Set();
  const merged = [];
  for (const m of [...fellows, ...direct, ...fromClaude]) {
    if (!seen.has(m.name)) {
      merged.push(m);
      seen.add(m.name);
    }
  }
  return merged.slice(0, isDesign ? 8 : 5);
}

async function buildAnswer(query) {
  const csvResp = await fetch(`${SHEET_CSV_URL}${SHEET_CSV_URL.includes('?') ? '&' : '?'}_ts=${Date.now()}`);
  if (!csvResp.ok) throw new Error(`Sheet fetch failed: ${csvResp.status}`);
  const csvText = await csvResp.text();
  const people = parseRoster(csvText);

  const matches = await findMatches(query, people);

  if (!matches || !matches.length) {
    return { text: `Couldn't find anyone in the directory for "${query}". Try rephrasing?` };
  }

  const byName = Object.fromEntries(people.map((p) => [p.name, p]));
  const annotated = matches.map((m) => {
    const person = byName[m.name];
    const offset = person ? parseEtOffset(person.location) : null;
    const local = offset !== null ? localTimeFor(offset) : null;
    const available = local ? isWorkingHours(local) : null;
    return {
      name: m.name,
      location: person ? person.location : '',
      localTime: local ? fmtTime(local) : null,
      available,
    };
  });
  annotated.sort((a, b) => (b.available === true) - (a.available === true));

  return buildResponse(query, annotated, byName);
}

async function postToSlack(channel, text, blocks) {
  const token = process.env.SLACK_BOT_TOKEN;
  if (!token) {
    console.error('SLACK_BOT_TOKEN is not set — cannot post the reply.');
    return;
  }
  const payload = { channel, text };
  if (blocks) payload.blocks = blocks;
  await fetch('https://slack.com/api/chat.postMessage', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).send('Method not allowed');
    return;
  }

  const rawBody = await readRawBody(req);
  let body;
  try {
    body = JSON.parse(rawBody);
  } catch {
    res.status(400).send('Bad request');
    return;
  }

  if (body.type === 'url_verification') {
    res.status(200).json({ challenge: body.challenge });
    return;
  }

  const ok = await verifySlackSignature(req, rawBody);
  if (!ok) {
    res.status(401).send('Invalid Slack signature');
    return;
  }

  if (req.headers['x-slack-retry-num']) {
    res.status(200).send('ok');
    return;
  }

  if (body.type === 'event_callback') {
    const event = body.event || {};
    const isDirectMessage = event.type === 'message' && event.channel_type === 'im';
    const isRealUserMessage = !event.bot_id && !event.subtype;

    if (isDirectMessage && isRealUserMessage && event.text) {
      try {
        const answer = await buildAnswer(event.text.trim());
        await postToSlack(event.channel, answer.text, answer.blocks);
      } catch (err) {
        await postToSlack(event.channel, `Something went wrong looking that up: ${err.message}`);
      }
    }
  }

  res.status(200).send('ok');
}
