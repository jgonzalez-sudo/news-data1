// /api/whoshelp.js
//
// Slack slash-command handler: "/help who can help me build a chart"
//
// Fully self-contained — does NOT depend on any other file in this repo.
//
// Flow:
//   1. Verify the request actually came from Slack (if SLACK_SIGNING_SECRET is set).
//   2. Fetch the published roster CSV directly.
//   3. Try to match the query against each person's "go_to_for" / teams text.
//      If nothing matches directly, ask Claude to pick from the roster instead.
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
// 3. Under "Slash Commands", create /help with Request URL:
//      https://news-data1.vercel.app/api/whoshelp
// 4. Install the app to your workspace.
// 5. Under "Basic Information" -> "App Credentials", copy the Signing Secret
//    and add it to Vercel as SLACK_SIGNING_SECRET, then redeploy.
//    (Without it the endpoint still works, it just can't verify the caller.)
// 6. Uses the ANTHROPIC_API_KEY already set in this Vercel project as a
//    fallback for queries that don't directly match anyone's "go to for" text.

export const config = {
  api: { bodyParser: false },
};

const WORK_START_HOUR = 9;   // 9am local
const WORK_END_HOUR = 17;    // 5pm local
const CLAUDE_MODEL = 'claude-haiku-4-5-20251001';
// Max wait for Claude before falling back to keyword matching.
const CLAUDE_TIMEOUT_MS = Number(process.env.CLAUDE_TIMEOUT_MS) || 2200;

// Published Google Sheet, CSV output — same one the whos-who site was built
// from. An env var of the same name overrides this if you ever set one.
const SHEET_CSV_URL = process.env.SHEET_CSV_URL ||
  'https://docs.google.com/spreadsheets/d/e/2PACX-1vRrtWiQmcJfaEE0W1NAyauZNLiDBttCP2Jk9jJGLJP0_K_YNcCAM-3zqUZWvZkzqFXSKHND3e_fP9pW/pub?gid=1852591954&single=true&output=csv';

const STOPWORDS = new Set(['who', 'can', 'help', 'me', 'i', 'a', 'an', 'the', 'with',
  'for', 'to', 'now', 'today', 'on', 'is', 'are', 'do', 'does', 'need', 'needs',
  'my', 'our', 'we', 'us', 'please', 'and', 'or', 'of', 'in', 'get', 'got']);

// Standing rule: fellows are always a go-to for design/visual work, regardless
// of what their individual "go to for" text says.
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

// Best-effort: map real names to Slack user IDs so names can be @mentioned
// (clickable, opens their profile card with a Message button). Requires the
// users:read scope — if that's not granted yet, this just returns {} and
// names render as plain bold text instead of failing the whole request.
// Common nicknames — lets "Victoria Kuhr" in the sheet resolve to a Slack
// profile literally named "Tori Kuhr". Grouped so any two names in the same
// group are treated as equivalent.
const NICKNAME_GROUPS = [
  ['victoria', 'tori', 'vicky', 'vicki'],
  ['robert', 'rob', 'bob', 'bobby'],
  ['william', 'will', 'bill', 'billy'],
  ['elizabeth', 'liz', 'beth', 'lizzie', 'eliza', 'betsy'],
  ['michael', 'mike', 'mikey'],
  ['jennifer', 'jen', 'jenny'],
  ['alexander', 'alex'],
  ['alexandra', 'alex', 'lexi', 'sasha'],
  ['nicholas', 'nick'],
  ['christopher', 'chris'],
  ['daniel', 'dan', 'danny'],
  ['matthew', 'matt'],
  ['andrew', 'andy', 'drew'],
  ['jonathan', 'jon', 'jonny'],
  ['samuel', 'sam'],
  ['benjamin', 'ben', 'benji'],
  ['katherine', 'kate', 'katie', 'kathy'],
  ['catherine', 'kate', 'katie', 'cathy'],
  ['jessica', 'jess', 'jessie'],
  ['amanda', 'mandy'],
  ['timothy', 'tim'],
  ['anthony', 'tony'],
  ['patricia', 'pat', 'patty', 'tricia'],
  ['margaret', 'maggie', 'meg', 'peggy'],
  ['joseph', 'joe', 'joey'],
  ['charles', 'charlie', 'chuck'],
  ['richard', 'rick', 'ricky'],
  ['thomas', 'tom', 'tommy'],
  ['james', 'jim', 'jimmy'],
  ['edward', 'ed', 'eddie', 'ted'],
  ['susan', 'sue', 'susie'],
  ['deborah', 'deb', 'debbie'],
  ['rebecca', 'becky', 'becca'],
  ['stephanie', 'steph'],
  ['gabriel', 'gabe'],
  ['nathaniel', 'nate'],
  ['zachary', 'zach'],
  ['jacqueline', 'jackie'],
  ['cynthia', 'cindy'],
  ['joshua', 'josh'],
  ['jacob', 'jake'],
  ['peter', 'pete'],
  ['patrick', 'pat'],
  ['gregory', 'greg'],
  ['kenneth', 'ken', 'kenny'],
  ['ronald', 'ron', 'ronnie'],
  ['raymond', 'ray'],
  ['lawrence', 'larry'],
  ['frederick', 'fred', 'freddie'],
  ['theodore', 'ted', 'teddy'],
  ['douglas', 'doug'],
];
const NICKNAME_GROUP_ID = {};
NICKNAME_GROUPS.forEach((group, i) => group.forEach((n) => { NICKNAME_GROUP_ID[n] = i; }));
function firstNamesEquivalent(a, b) {
  if (a === b) return true;
  return NICKNAME_GROUP_ID[a] !== undefined && NICKNAME_GROUP_ID[a] === NICKNAME_GROUP_ID[b];
}

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1]);
    }
  }
  return dp[m][n];
}

async function fetchSlackUserMap() {
  const token = process.env.SLACK_BOT_TOKEN;
  const empty = { byFullName: {}, bySurname: {}, members: [] };
  if (!token) return empty;

  try {
    // Paginate through the full workspace directory — a single 200-person
    // page silently missed anyone past it, which is why some exact-name
    // matches (not just nickname cases) were failing.
    let rawMembers = [];
    let cursor = '';
    let pages = 0;
    do {
      const url = `https://slack.com/api/users.list?limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`;
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      const data = await resp.json();
      if (!data.ok) break;
      rawMembers = rawMembers.concat(data.members || []);
      cursor = (data.response_metadata && data.response_metadata.next_cursor) || '';
      pages++;
    } while (cursor && pages < 10);

    const byFullName = {};
    const surnameCounts = {};
    const surnameId = {};
    const members = [];

    rawMembers.forEach((m) => {
      const real = (m.profile && (m.profile.real_name || m.profile.display_name)) || m.real_name || '';
      if (!real) return;
      const norm = real.trim().toLowerCase().replace(/\s+/g, ' ');
      const parts = norm.split(' ');
      const first = parts[0];
      const last = parts[parts.length - 1];
      byFullName[norm] = m.id;
      surnameCounts[last] = (surnameCounts[last] || 0) + 1;
      surnameId[last] = m.id;
      members.push({ norm, first, last, id: m.id });
    });

    const bySurname = {};
    Object.keys(surnameCounts).forEach((s) => {
      if (surnameCounts[s] === 1) bySurname[s] = surnameId[s];
    });

    return { byFullName, bySurname, members };
  } catch {
    return empty;
  }
}

function lookupSlackId(name, slackUserMap) {
  const full = name.trim().toLowerCase().replace(/\s+/g, ' ');
  if (slackUserMap.byFullName[full]) return slackUserMap.byFullName[full];

  const parts = full.split(' ');
  const first = parts[0];
  const last = parts[parts.length - 1];

  // Same surname + matching or nickname-equivalent first name — safer than
  // a bare surname match since it still works when a surname isn't unique.
  const sameSurname = slackUserMap.members.filter((m) => m.last === last);
  const nicknameMatches = sameSurname.filter((m) => firstNamesEquivalent(m.first, first));
  if (nicknameMatches.length === 1) return nicknameMatches[0].id;

  // Surname alone, only when it belongs to exactly one person company-wide.
  if (slackUserMap.bySurname[last]) return slackUserMap.bySurname[last];

  // Last resort: closest full-name spelling match — only accepted if it's a
  // clear, unambiguous winner (small distance, and meaningfully closer than
  // the next-best candidate), so a genuine uncertainty never guesses wrong.
  let best = null;
  let bestDist = Infinity;
  let secondDist = Infinity;
  slackUserMap.members.forEach((m) => {
    const d = levenshtein(full, m.norm);
    if (d < bestDist) {
      secondDist = bestDist;
      bestDist = d;
      best = m;
    } else if (d < secondDist) {
      secondDist = d;
    }
  });
  if (best && bestDist <= 2 && secondDist - bestDist >= 2) return best.id;

  return null;
}

function personLine(a, slackUserMap) {
  const id = lookupSlackId(a.name, slackUserMap);
  const namePart = id ? `<@${id}>` : `*${a.name}*`;
  return `${namePart} — ${a.location || 'location unknown'}${a.localTime ? ` — ${a.localTime}` : ''}`;
}

function personBlock(a, byName, slackUserMap) {
  const person = byName[a.name];
  let text = personLine(a, slackUserMap);

  const subline = person && (person.title || person.go_to_for)
    ? [person.title, person.go_to_for].filter(Boolean).join(' · ')
    : null;
  if (subline) text += `\n_${subline}_`;

  const block = {
    type: 'section',
    text: { type: 'mrkdwn', text },
  };
  if (person && person.photo_url) {
    block.accessory = { type: 'image', image_url: person.photo_url, alt_text: a.name };
  }
  return block;
}

function buildResponse(query, annotated, byName, slackUserMap) {
  const etNow = fmtTime(nowEtParts());
  const available = annotated.filter((a) => a.available === true);
  const others = annotated.filter((a) => a.available !== true);

  const blocks = [
    { type: 'section', text: { type: 'mrkdwn', text: `*Who can help — "${query}"*  _(as of ${etNow} ET)_` } },
  ];

  if (available.length) {
    blocks.push({ type: 'section', text: { type: 'mrkdwn', text: '*Available now:*' } });
    available.forEach((a) => blocks.push(personBlock(a, byName, slackUserMap)));
  }
  if (others.length) {
    blocks.push({ type: 'divider' });
    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: available.length ? '*Also can help (outside typical hours or unknown location):*' : '*Can help:*' },
    });
    others.forEach((a) => blocks.push(personBlock(a, byName, slackUserMap)));
  }

  const fallbackText = `Who can help — "${query}" (as of ${etNow} ET): ` +
    [...available, ...others].map((a) => a.name).join(', ');

  return { text: fallbackText, blocks };
}

// Common short-form terms that would otherwise get lost — "T&E" splits into
// single letters and gets discarded by the length filter below, leaving the
// query with nothing to search on.
const ABBREVIATION_EXPANSIONS = [
  [/\bt\s*&\s*e\b/g, 'travel expense expenses reimbursement'],
  [/\bhr\b/g, 'human resources hr'],
  [/\bit\b/g, 'information technology it tech support'],
  [/\bpr\b/g, 'public relations pr'],
];

function expandAbbreviations(text) {
  let t = text.toLowerCase();
  ABBREVIATION_EXPANSIONS.forEach(([pattern, expansion]) => {
    t = t.replace(pattern, expansion);
  });
  return t;
}

function tokenize(text) {
  const expanded = expandAbbreviations(text);
  return (expanded.match(/[a-z0-9]+/g) || []).filter((w) => w.length > 1 && !STOPWORDS.has(w));
}

// First pass: keyword overlap against each person's go_to_for/teams/bio text.
// Matches on whole tokens (not substrings) so short words like "hr" or "it"
// don't false-positive match inside unrelated longer words.
function keywordMatch(query, people) {
  const tokens = tokenize(query);
  if (!tokens.length) return [];

  const scored = people.map((p) => {
    const haystackText = `${p.go_to_for} ${p.teams.join(' ')} ${p.bio}`;
    const haystackTokens = new Set(tokenize(haystackText));
    const score = tokens.reduce((s, t) => s + (haystackTokens.has(t) ? 1 : 0), 0);
    return { p, score };
  }).filter((x) => x.score > 0);

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, 5).map((x) => ({ name: x.p.name, reason: x.p.go_to_for || x.p.bio || '' }));
}

// Fallback: ask Claude to pick from the roster when keyword matching finds nothing.
const GUIDANCE_CSV_URL = process.env.GUIDANCE_CSV_URL || '';

// Standing instructions given to Claude on every question. Add more from a
// "Guidance" tab in the sheet (one note per row, header: guidance).
const DEFAULT_GUIDANCE = [
  'People whose title contains "Fellow" are the go-to for charts, Figma, graphics and other design or visual requests.',
  '"Help with tech" or similar means technical support (CMS, Google access, accounts, crashes, hardware) - NOT the Tech newsletter or tech coverage - unless the person mentions the newsletter, the team or coverage.',
  'Prefer people whose "go to for" directly addresses the request. Return fewer people rather than guessing; return [] if no one clearly fits.',
  'Do not consider time zones or availability - that is handled separately.',
  'Return up to 5 people (up to 8 for chart, graphics or design requests), best match first.',
];

async function fetchGuidance() {
  if (!GUIDANCE_CSV_URL) return [];
  try {
    const resp = await fetch(`${GUIDANCE_CSV_URL}${GUIDANCE_CSV_URL.includes('?') ? '&' : '?'}_ts=${Date.now()}`);
    if (!resp.ok) return [];
    const rows = parseCSV(await resp.text());
    if (rows.length < 2) return [];
    const idx = Math.max(0, rows[0].map((h) => h.trim().toLowerCase()).indexOf('guidance'));
    return rows.slice(1).map((r) => (r[idx] || '').trim()).filter(Boolean);
  } catch {
    return [];
  }
}

async function claudeMatch(query, people, opts = {}) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return [];

  const guidance = [...DEFAULT_GUIDANCE, ...(opts.guidance || [])];
  const rosterText = people
    .map((p) => `${p.name} | ${p.title || 'n/a'} | Teams: ${p.teams.join(', ') || 'n/a'} | ${p.location || 'n/a'} | Go to for: ${p.go_to_for || p.bio || 'n/a'}`)
    .join('\n');
  const hintText = opts.hints && opts.hints.length
    ? `\n\nKeyword-search candidates (hints only - may be wrong, verify against the roster):\n${opts.hints.map((h) => `- ${h.name}`).join('\n')}`
    : '';
  const techNote = opts.techSupport
    ? '\n\nNote: this question looks like a request for technical/IT support, not about the Tech newsletter.'
    : '';

  const systemPrompt = `You are the lookup assistant for Semafor's internal staff directory. A colleague asks who can help with something. ` +
    `Work out what they actually need, then pick the best people using ONLY the roster below.\n\n` +
    `Guidance (follow these):\n${guidance.map((g) => `- ${g}`).join('\n')}\n\n` +
    `Respond with ONLY a JSON array like [{"name":"Full Name","reason":"short reason"}], best match first, ` +
    `with names exactly as they appear in the roster. Return [] if nobody fits.\n\n` +
    `Roster (name | title | teams | location | go to for):\n${rosterText}${hintText}${techNote}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs || CLAUDE_TIMEOUT_MS);
  try {
    const resp = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: CLAUDE_MODEL,
        max_tokens: 600,
        system: systemPrompt,
        messages: [{ role: 'user', content: query }],
      }),
    });
    if (!resp.ok) throw new Error(`Claude API failed: ${resp.status}`);
    const data = await resp.json();
    const textBlock = (data.content || []).find((b) => b.type === 'text');
    if (!textBlock) return [];
    const parsed = JSON.parse(textBlock.text.replace(/```json|```/g, '').trim());
    return Array.isArray(parsed) ? parsed.filter((m) => m && m.name) : [];
  } catch {
    return [];
  } finally {
    clearTimeout(timer);
  }
}

// Detects "who's on the X team" style questions and pulls the answer
// directly from each person's structured `teams` tags, rather than the
// fuzzy text search — which was wrongly sweeping in anyone whose go_to_for
// text happened to *mention* a team name (e.g. a business-side person doing
// Gulf-market sponsorships) alongside the actual Gulf editorial team.
function extractTeamQuery(query) {
  const q = query.toLowerCase();
  const patterns = [
    /\bwho\s+(?:works?|is|are)\s+(?:in|on)\s+(?:the\s+)?([a-z0-9&,\s]+?)\s*team\b/,
    /\bwho(?:'s| is| are)\s+on\s+(?:the\s+)?([a-z0-9&,\s]+?)\s*team\b/,
    /\b(?:members?|people)\s+(?:of|in|on)\s+(?:the\s+)?([a-z0-9&,\s]+?)\s*team\b/,
    /\bwho\s+is\s+on\s+([a-z0-9&,\s]+?)\s*$/,
  ];
  for (const p of patterns) {
    const m = q.match(p);
    if (m && m[1] && m[1].trim()) return m[1].trim();
  }
  return null;
}

function matchByTeam(query, people) {
  const teamQuery = extractTeamQuery(query);
  if (!teamQuery) return null;

  const allTeams = new Set();
  people.forEach((p) => p.teams.forEach((t) => allTeams.add(t)));

  let matchedTeam = null;
  for (const t of allTeams) {
    if (t.toLowerCase() === teamQuery) { matchedTeam = t; break; }
  }
  if (!matchedTeam) {
    for (const t of allTeams) {
      if (t.toLowerCase().includes(teamQuery) || teamQuery.includes(t.toLowerCase())) {
        matchedTeam = t;
        break;
      }
    }
  }
  if (!matchedTeam) return null;

  const members = people.filter((p) => p.teams.some((t) => t.toLowerCase() === matchedTeam.toLowerCase()));
  if (!members.length) return null;

  return members.map((p) => ({ name: p.name, reason: `${matchedTeam} team` }));
}


// ---- "Taught" answers: curated rules from a Rules tab in the Google Sheet ----
// Each row: keywords (comma-separated phrases) | people (comma-separated full
// names, as they appear in the roster) | note (optional). If a question
// contains any keyword phrase, those people are returned as the answer and
// the fuzzy matching is skipped. Leave RULES_CSV_URL empty to disable.
const RULES_CSV_URL = process.env.RULES_CSV_URL || '';

function normalizePhrase(s) {
  return ' ' + String(s).toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim() + ' ';
}

async function fetchRules() {
  if (!RULES_CSV_URL) return [];
  try {
    const resp = await fetch(`${RULES_CSV_URL}${RULES_CSV_URL.includes('?') ? '&' : '?'}_ts=${Date.now()}`);
    if (!resp.ok) return [];
    const rows = parseCSV(await resp.text());
    if (rows.length < 2) return [];
    const headers = rows[0].map((h) => h.trim().toLowerCase());
    const col = (name) => headers.indexOf(name);
    return rows.slice(1).map((r) => ({
      keywords: (r[col('keywords')] || '').split(',').map((k) => normalizePhrase(k)).filter((k) => k.trim()),
      people: (r[col('people')] || '').split(',').map((n) => n.trim()).filter(Boolean),
      note: (r[col('note')] || '').trim(),
    })).filter((r) => r.keywords.length && r.people.length);
  } catch {
    return [];
  }
}

async function matchByRules(query, people) {
  const rules = await fetchRules();
  if (!rules.length) return null;
  const q = normalizePhrase(query);
  const byLower = Object.fromEntries(people.map((p) => [p.name.toLowerCase(), p]));
  for (const rule of rules) {
    if (rule.keywords.some((k) => q.includes(k))) {
      const found = rule.people
        .map((n) => byLower[n.toLowerCase()])
        .filter(Boolean)
        .map((p) => ({ name: p.name, reason: rule.note || 'Recommended contact' }));
      if (found.length) return found;
    }
  }
  return null;
}


// ---- Tech *support* vs. Tech *newsletter* ----
// "Help with tech", "CMS is down", "locked out of Google" are requests for
// technical support — not questions about the Tech newsletter or its team.
// Detected here so they're answered from IT/tech-support people instead.
const TECH_NEWSLETTER_HINTS = /\b(tech\s+(newsletter|team|section|reporters?|coverage|desk|beat)|(cover|covers|covering|write|writes|writing|report|reports|reporting)\s+(on\s+)?(tech|ai))\b/;
const TECH_SUPPORT_HINTS = [
  /\b(help|issue|issues|problem|problems|trouble|support|fix|broken|stuck)\b.*\b(tech|technology)\b/,
  /\b(tech|technology)\s+(help|issue|issues|problem|problems|support|trouble)\b/,
  /\b(cms|vpn|wifi|wi-fi|password|login|log in|laptop|2fa|locked out|crash\w*)\b/,
  /\bgoogle\s+(access|drive|doc|docs|sheet|sheets|account)\b/,
];
const TECH_SUPPORT_TOKENS = new Set(['it', 'tech', 'technical', 'technology', 'cms', 'google',
  'laptop', 'computer', 'hardware', 'software', 'systems', 'security', 'vpn', 'wifi',
  'helpdesk', 'engineer', 'engineering', 'developer', 'wordpress', 'accounts', 'access']);

function isTechSupportQuery(query) {
  const q = query.toLowerCase();
  if (TECH_NEWSLETTER_HINTS.test(q)) return false;
  return TECH_SUPPORT_HINTS.some((re) => re.test(q));
}

// Looks at title + go_to_for + bio only — deliberately NOT the teams tags,
// so everyone on the "Tech" newsletter team doesn't match.
function techSupportMatch(people) {
  const scored = people.map((p) => {
    const tokens = new Set(tokenize(`${p.title} ${p.go_to_for} ${p.bio}`));
    let score = 0;
    TECH_SUPPORT_TOKENS.forEach((t) => { if (tokens.has(t)) score += 1; });
    return { p, score };
  }).filter((x) => x.score > 0);
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, 5).map((x) => ({ name: x.p.name, reason: x.p.go_to_for || x.p.title || '' }));
}


// Claude is the primary judge. Order: curated Rules tab (override) ->
// structured team lookup -> Claude (with guidance + keyword hints) -> the
// older keyword logic as a fallback if Claude is slow, down or returns nothing.
async function findMatches(query, people) {
  const guidancePromise = fetchGuidance();

  const ruleMatches = await matchByRules(query, people);
  if (ruleMatches) return ruleMatches;

  const teamMatches = matchByTeam(query, people);
  if (teamMatches && teamMatches.length) return teamMatches.slice(0, 15);

  const guidance = await guidancePromise;
  const isDesign = isDesignQuery(query);
  const picks = await claudeMatch(query, people, {
    guidance,
    hints: keywordMatch(query, people),
    techSupport: isTechSupportQuery(query),
    timeoutMs: CLAUDE_TIMEOUT_MS,
  });
  const known = new Set(people.map((p) => p.name));
  const valid = picks.filter((m) => known.has(m.name));
  if (valid.length) return valid.slice(0, isDesign ? 8 : 5);

  return legacyMatches(query, people);
}

async function legacyMatches(query, people) {
  if (isTechSupportQuery(query)) {
    const direct = techSupportMatch(people);
    let fromClaude = [];
    try {
      fromClaude = await claudeMatch(
        `${query} (This is a request for technical/IT support — e.g. CMS, accounts, access, hardware, crashes — NOT about the Tech newsletter or tech coverage.)`,
        people
      );
    } catch {
      fromClaude = [];
    }
    const seen = new Set();
    const merged = [];
    for (const m of [...direct, ...fromClaude]) {
      if (!seen.has(m.name)) { merged.push(m); seen.add(m.name); }
    }
    return merged.slice(0, 5);
  }

  const teamMatches = matchByTeam(query, people);
  if (teamMatches && teamMatches.length) return teamMatches.slice(0, 15);

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

  // Slack gives up after 3 seconds, but the lookup (sheet, rules, Claude,
  // Slack user list) takes longer. So: answer Slack right away, finish the
  // lookup in the background, and post the result to Slack's response_url.
  const responseUrl = params.get('response_url');
  const vercelCtx = globalThis[Symbol.for('@vercel/request-context')]?.get?.();

  if (responseUrl && vercelCtx && typeof vercelCtx.waitUntil === 'function') {
    res.status(200).json({ response_type: 'ephemeral', text: `Looking up "${query}"…` });
    vercelCtx.waitUntil(
      buildHelpPayload(query).then((payload) =>
        fetch(responseUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...payload, replace_original: true }),
        })
      ).catch((err) => console.error('whoshelp background error', err))
    );
    return;
  }

  // Fallback: old behaviour (answer only once the lookup is done).
  res.status(200).json(await buildHelpPayload(query));
}

async function buildHelpPayload(query) {
  try {
    const csvResp = await fetch(`${SHEET_CSV_URL}${SHEET_CSV_URL.includes('?') ? '&' : '?'}_ts=${Date.now()}`);
    if (!csvResp.ok) throw new Error(`Sheet fetch failed: ${csvResp.status}`);
    const csvText = await csvResp.text();
    const people = parseRoster(csvText);

    const matches = await findMatches(query, people);

    if (!matches || !matches.length) {
      return {
        response_type: 'ephemeral',
        text: `Couldn't find anyone in the directory for "${query}". Try rephrasing?`,
      };
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

    const slackUserMap = await fetchSlackUserMap();
    const { text, blocks } = buildResponse(query, annotated, byName, slackUserMap);

    return { response_type: 'ephemeral', text, blocks };
  } catch (err) {
    return {
      response_type: 'ephemeral',
      text: `Something went wrong looking that up: ${err.message}`,
    };
  }
}
