// /api/whoshelp.js
//
// Slack slash-command handler: "/help who can help me build a chart"
//
// Flow:
//   1. Verify the request actually came from Slack (if SLACK_SIGNING_SECRET is set).
//   2. Fetch the published roster CSV directly (same source the whos-who site
//      was originally built from — this runs server-side, so it doesn't need
//      the Google-login step your site's UI added for browser visitors).
//   3. Call your existing /api/ask with { query, people } to get name matches
//      (reuses the KNOWN_ANSWERS rules + Claude fallback you already built).
//   4. For each match, work out their current local time from the "City · ET+N"
//      offset stored in their location field, and flag whether it's inside
//      normal working hours right now.
//   5. Reply directly in the HTTP response Slack is waiting on — no bot token,
//      no Events API, nothing async.
//
// If your published Sheet URL ever changes, update SHEET_CSV_URL below (or set
// it as a Vercel env var of the same name — that takes priority if present).
//
// ---- one-time setup ----
// 1. Deploy this file to the news-data1 repo at api/whoshelp.js.
// 2. Create a Slack app at https://api.slack.com/apps -> From scratch.
// 4. Under "Slash Commands", create /help with Request URL:
//      https://news-data1.vercel.app/api/whoshelp
//    (Note: /help is a common name — Slack allows it, but if another app in
//    your workspace already registers /help, Slack routes to whichever was
//    installed most recently. Test it after install to confirm it hits this
//    endpoint and not something else. If it collides, /whoshelp is the safer
//    fallback name.)
// 5. Install the app to your workspace.
// 6. Under "Basic Information" -> "App Credentials", copy the Signing Secret
//    and add it to Vercel as SLACK_SIGNING_SECRET, then redeploy.
//    (Without it the endpoint still works, it just can't verify the caller.)

export const config = {
  api: { bodyParser: false },
};

const WORK_START_HOUR = 8;   // 8am local
const WORK_END_HOUR = 19;    // 7pm local
const ASK_ENDPOINT = process.env.ASK_ENDPOINT_URL || 'https://news-data1.vercel.app/api/ask';

// Published Google Sheet, CSV output — same one the whos-who site was built
// from. An env var of the same name overrides this if you ever set one.
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
  if (!secret) return true; // not configured yet — allow through, but see setup step 6

  const crypto = await import('crypto');
  const timestamp = req.headers['x-slack-request-timestamp'];
  const slackSig = req.headers['x-slack-signature'];
  if (!timestamp || !slackSig) return false;

  // Reject requests older than 5 minutes (replay protection)
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 60 * 5) return false;

  const baseString = `v0:${timestamp}:${rawBody}`;
  const hmac = crypto.createHmac('sha256', secret).update(baseString).digest('hex');
  const computedSig = `v0=${hmac}`;

  if (computedSig.length !== slackSig.length) return false;
  return crypto.timingSafeEqual(Buffer.from(computedSig), Buffer.from(slackSig));
}

// ---- minimal RFC4180 CSV parser (handles quoted fields with commas/newlines) ----
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
      } else {
        field += c;
      }
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

// "New York · ET" -> 0, "London · ET+5" -> 5, "San Francisco · ET-3" -> -3
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
  const day = localDate.getDay(); // 0 = Sun, 6 = Sat
  const hour = localDate.getHours();
  return day >= 1 && day <= 5 && hour >= WORK_START_HOUR && hour < WORK_END_HOUR;
}

function fmtTime(d) {
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).send('Method not allowed');
    return;
  }

  const rawBody = await readRawBody(req);
  const ok = await verifySlackSignature(req, rawBody);
  if (!ok) {
    res.status(401).send('Invalid Slack signature');
    return;
  }

  const params = new URLSearchParams(rawBody);
  const query = (params.get('text') || '').trim();

  if (!query) {
    res.status(200).json({
      response_type: 'ephemeral',
      text: 'Ask me something like `/help who can help me build a chart`.',
    });
    return;
  }

  try {
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
      res.status(200).json({
        response_type: 'ephemeral',
        text: `Couldn't find anyone in the directory for "${query}". Try rephrasing, or it may need a new rule in api/ask.js.`,
      });
      return;
    }

    const byName = Object.fromEntries(people.map((p) => [p.name, p]));
    const annotated = matches.map((m) => {
      const person = byName[m.name];
      const offset = person ? parseEtOffset(person.location) : null;
      const local = offset !== null ? localTimeFor(offset) : null;
      const available = local ? isWorkingHours(local) : null;
      return {
        name: m.name,
        reason: m.reason,
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

    res.status(200).json({ response_type: 'ephemeral', text });
  } catch (err) {
    res.status(200).json({
      response_type: 'ephemeral',
      text: `Something went wrong looking that up: ${err.message}`,
    });
  }
}
