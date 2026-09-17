// /api/slack-events.js
//
// Lets people DM the Slack app directly (e.g. "who can help me build a chart")
// instead of typing a slash command. Same underlying lookup as /api/whoshelp,
// just triggered by a direct message instead of "/help ...".
//
// Reads the roster from the published Sheet CSV directly (SHEET_CSV_URL
// below) — this runs server-side, so it skips the Google-login step your
// site's UI added for browser visitors.
//
// ---- one-time setup (in addition to what /help already needed) ----
// 1. Deploy this file to news-data1 at api/slack-events.js.
// 2. In the Slack app settings, left sidebar -> "OAuth & Permissions":
//    - Under "Scopes" -> "Bot Token Scopes", add:
//        chat:write   (so the app can send DM replies)
//        im:history   (so the app receives DM messages)
//    - Click "Reinstall to Semafor" at the top of that page (adding scopes
//      requires reinstalling). After reinstalling, copy the new
//      "Bot User OAuth Token" (starts with xoxb-) and add it to Vercel as
//      SLACK_BOT_TOKEN, then redeploy.
// 3. Left sidebar -> "App Home": turn on "Messages Tab", and check
//    "Allow users to send Slash commands and messages from the messages tab".
//    This is what makes the app show up as a chat you can open, like the
//    screenshot.
// 4. Left sidebar -> "Event Subscriptions": turn Events on. Request URL:
//      https://news-data1.vercel.app/api/slack-events
//    Slack will immediately send a verification ping — this file answers it
//    automatically, so the URL should show "Verified" within a second or two.
// 5. Still on that page, under "Subscribe to bot events", add: message.im
//    Save, then reinstall the app again if prompted.
//
// That's the whole setup — no separate server, still just Vercel functions.

export const config = {
  api: { bodyParser: false },
};

const WORK_START_HOUR = 8;
const WORK_END_HOUR = 19;
const ASK_ENDPOINT = process.env.ASK_ENDPOINT_URL || 'https://news-data1.vercel.app/api/ask';

// Published Google Sheet, CSV output — same one the whos-who site was built
// from, and the same constant used in api/whoshelp.js. An env var of the same
// name overrides this if you ever set one.
const SHEET_CSV_URL = process.env.SHEET_CSV_URL ||
  'https://docs.google.com/spreadsheets/d/e/2PACX-1vRrtWiQmcJfaEE0W1NAyauZNLiDBttCP2Jk9jJGLJP0_K_YNcCAM-3zqUZWvZkzqFXSKHND3e_fP9pW/pub?gid=1852591954&single=true&output=csv';

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
  if (!secret) return true; // same caveat as whoshelp.js — add this before relying on it

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

// ---- same CSV/roster/time helpers as whoshelp.js (kept duplicated on purpose —
//      these two files are meant to be droppable independently; if you tweak the
//      matching or time logic, update both) ----

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

async function buildAnswer(query) {
  const csvResp = await fetch(`${SHEET_CSV_URL}${SHEET_CSV_URL.includes('?') ? '&' : '?'}_ts=${Date.now()}`);
  if (!csvResp.ok) throw new Error(`Sheet fetch failed: ${csvResp.status}`);
  const csvText = await csvResp.text();
  const people = parseRoster(csvText);

  const askResp = await fetch(ASK_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, people }),
  });
  if (!askResp.ok) throw new Error(`/api/ask failed: ${askResp.status}`);
  const { matches } = await askResp.json();

  if (!matches || !matches.length) {
    return `Couldn't find anyone in the directory for "${query}". Try rephrasing?`;
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

  const etNow = fmtTime(nowEtParts());
  const available = annotated.filter((a) => a.available === true);
  const others = annotated.filter((a) => a.available !== true);

  let text = `*Who can help — "${query}"*  _(as of ${etNow} ET)_\n\n`;
  if (available.length) {
    text += '*🟢 Available now:*\n';
    text += available.map((a) => `• *${a.name}* — ${a.location || 'location unknown'}${a.localTime ? ` — ${a.localTime}` : ''}`).join('\n');
    text += '\n\n';
  }
  if (others.length) {
    text += available.length ? '*Also can help (outside typical hours or unknown location):*\n' : '*Can help:*\n';
    text += others.map((a) => `• *${a.name}* — ${a.location || 'location unknown'}${a.localTime ? ` — ${a.localTime}` : ''}`).join('\n');
  }
  return text;
}

async function postToSlack(channel, text) {
  const token = process.env.SLACK_BOT_TOKEN;
  if (!token) {
    console.error('SLACK_BOT_TOKEN is not set — cannot post the reply.');
    return;
  }
  await fetch('https://slack.com/api/chat.postMessage', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ channel, text }),
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

  // Slack's one-time handshake when you first save the Request URL.
  if (body.type === 'url_verification') {
    res.status(200).json({ challenge: body.challenge });
    return;
  }

  const ok = await verifySlackSignature(req, rawBody);
  if (!ok) {
    res.status(401).send('Invalid Slack signature');
    return;
  }

  // Slack retries events that don't get a fast-enough ack — ignore retries
  // so we don't send the same answer twice into someone's DM.
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
        await postToSlack(event.channel, answer);
      } catch (err) {
        await postToSlack(event.channel, `Something went wrong looking that up: ${err.message}`);
      }
    }
  }

  res.status(200).send('ok');
}
