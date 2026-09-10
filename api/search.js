// Each rule is checked in order against the lowercased question.
// First match wins, so more specific rules (a narrower scenario for a tool
// that also has a general rule) must come before the general one.
const KNOWN_ANSWERS = [
  {
    test: q => q.includes('reuters'),
    matches: [
      { name: 'Marta Biino', reason: 'Direct go-to for Reuters accounts and points.' },
      { name: 'Jer\u00f3nimo Gonz\u00e1lez', reason: 'Also handles new accounts with outside providers generally.' },
    ],
  },
  {
    test: q => q.includes('rippling'),
    matches: [
      { name: 'Alana Fallis', reason: 'HR manager \u2014 go-to for Rippling issues.' },
    ],
  },
  {
    // Urgent publishing need while SOUP is inaccessible -> different people
    // than a general SOUP bug report, so this must come before the generic
    // SOUP rules below.
    test: q => q.includes('soup') && /urgent|can.?t access|locked out/.test(q),
    matches: [
      { name: 'Marta Biino', reason: 'Can help get an urgent story published if SOUP is down.' },
      { name: 'Mary Lawrence Ware', reason: 'Can help get an urgent story published if SOUP is down.' },
      { name: 'Jake Angelo', reason: 'Can help get an urgent story published if SOUP is down.' },
    ],
  },
  {
    test: q => q.includes('soup') && /train|learn|teach|onboard/.test(q),
    matches: [
      { name: 'Marta Biino', reason: 'Runs SOUP (the CMS) training.' },
    ],
  },
  {
    test: q => q.includes('soup'),
    matches: [
      { name: 'Kellen Henry', reason: 'Go-to for SOUP issues on the product side.' },
      { name: 'Mark Wilkie', reason: 'Go-to for SOUP issues on the technical side.' },
    ],
  },
  {
    test: q => q.includes('figma') && /train|learn|teach|onboard/.test(q),
    matches: [
      { name: 'Marta Biino', reason: 'Runs Figma training.' },
    ],
  },
  {
    test: q => q.includes('figma'),
    matches: [
      { name: 'Kellen Henry', reason: 'Go-to for Figma problems.' },
    ],
  },
  {
    test: q => /datawrapper|data wrapper/.test(q) && /train|learn|teach|onboard/.test(q),
    matches: [
      { name: 'Marta Biino', reason: 'Runs DataWrapper training.' },
    ],
  },
  {
    test: q => /datawrapper|data wrapper/.test(q) && /new account|access|set ?up|provision/.test(q),
    matches: [
      { name: 'Jer\u00f3nimo Gonz\u00e1lez', reason: 'Sets up new DataWrapper accounts.' },
    ],
  },
  {
    test: q => /newsletter/.test(q) && /didn.?t|not (going|sending)|failed|late|broke/.test(q),
    matches: [
      { name: 'Mark Wilkie', reason: 'Go-to when a newsletter fails to send.' },
    ],
  },
  {
    test: q => /newsletter|ad\b/.test(q) && /glitch|wrong|typo|broken/.test(q),
    matches: [
      { name: 'Marta Biino', reason: 'Can help fix a broken or incorrect newsletter ad.' },
      { name: 'Jer\u00f3nimo Gonz\u00e1lez', reason: 'Can help fix a broken or incorrect newsletter ad.' },
      // Anthony Adiletta -> add once he's in the sheet:
      // { name: 'Anthony Adiletta', reason: 'Can help fix a broken or incorrect newsletter ad.' },
    ],
  },
  {
    test: q => /outlet|subscription/.test(q) && /login|log ?in|password|access|expired/.test(q),
    matches: [
      { name: 'Marta Biino', reason: 'Handles logins/subscriptions for outside media outlets.' },
      { name: 'Jer\u00f3nimo Gonz\u00e1lez', reason: 'Handles logins/subscriptions for outside media outlets.' },
    ],
  },
  {
    test: q => /semafor account|locked out|log(ged)? ?out|can.?t get back in/.test(q),
    matches: [
      { name: 'Alana Fallis', reason: 'HR manager \u2014 go-to for account lockouts.' },
    ],
  },
  {
    test: q => q.includes('laptop'),
    matches: [
      { name: 'Alana Fallis', reason: 'HR manager \u2014 go-to for laptop/hardware issues.' },
    ],
  },
  {
    test: q => /mistake|error|correction/.test(q) && /story|article|piece/.test(q),
    matches: [
      { name: 'Marta Biino', reason: 'Go-to for flagging a mistake in a published story.' },
      { name: 'Jer\u00f3nimo Gonz\u00e1lez', reason: 'Go-to for flagging a mistake in a published story.' },
    ],
  },
  {
    test: q => q.includes('photo') && /event/.test(q),
    matches: [
      { name: 'Marta Biino', reason: 'Go-to for photos from past Semafor events.' },
    ],
  },
  {
    test: q => q.includes('transcript'),
    matches: [
      { name: 'J.D. Capelouto', reason: 'Go-to for transcripts of past Semafor events.' },
    ],
  },
  {
    test: q => q.includes('schedule') && /event/.test(q),
    matches: [
      { name: 'Gina Chon', reason: 'Go-to for the schedule of an upcoming Semafor event.' },
    ],
  },
  {
    test: q => /style guide|copy ?edit/.test(q),
    matches: [
      { name: 'Gina Chua', reason: 'Go-to for style guide and copyediting questions.' },
      { name: 'Graph Massara', reason: 'Go-to for style guide and copyediting questions.' },
      // Shelly [LAST NAME NEEDED] -> add once confirmed and in the sheet:
      // { name: 'Shelly Lastname', reason: 'Go-to for style guide and copyediting questions.' },
    ],
  },
  {
    test: q => /\bai\b|chatbot/.test(q),
    matches: [
      { name: 'J.D. Capelouto', reason: 'Go-to for AI tool access and building new AI tools.' },
      { name: 'Jer\u00f3nimo Gonz\u00e1lez', reason: 'Go-to for AI tool access and building new AI tools.' },
    ],
  },
  {
    test: q => q.includes('graphic'),
    matches: [
      { name: 'Eugenia Perozo Paoli', reason: 'Can help produce a graphic for a story.' },
      { name: 'Mary Lawrence Ware', reason: 'Can help produce a graphic for a story.' },
      { name: 'Lauren Morganbesser', reason: 'Can help produce a graphic for a story.' },
      { name: 'Leah Quinn', reason: 'Can help produce a graphic for a story.' },
      { name: 'Jake Angelo', reason: 'Can help produce a graphic for a story.' },
      { name: 'Graph Massara', reason: 'Can help produce a graphic for a story.' },
    ],
  },
  {
    test: q => q.includes('chart'),
    matches: [
      { name: 'Eugenia Perozo Paoli', reason: 'Can help produce a chart for a story.' },
      { name: 'Mary Lawrence Ware', reason: 'Can help produce a chart for a story.' },
      { name: 'Lauren Morganbesser', reason: 'Can help produce a chart for a story.' },
      { name: 'Leah Quinn', reason: 'Can help produce a chart for a story.' },
      { name: 'Jake Angelo', reason: 'Can help produce a chart for a story.' },
    ],
  },
  {
    test: q => /expens/.test(q),
    matches: [
      { name: 'Jer\u00f3nimo Gonz\u00e1lez', reason: 'Go-to for expensing questions.' },
      // Samantha [LAST NAME NEEDED] -> add once confirmed and in the sheet:
      // { name: 'Samantha Lastname', reason: 'Go-to for expensing questions.' },
    ],
  },
  {
    test: q => q.includes('staffing'),
    matches: [
      { name: 'Jer\u00f3nimo Gonz\u00e1lez', reason: 'Go-to for staffing changes.' },
      // Shelly [LAST NAME NEEDED] -> add once confirmed and in the sheet:
      // { name: 'Shelly Lastname', reason: 'Go-to for staffing changes.' },
    ],
  },
  {
    // Generic catch-all: any training question not already caught by a
    // tool-specific rule above.
    test: q => q.includes('training'),
    matches: [
      { name: 'Marta Biino', reason: 'Handles training and onboarding questions generally.' },
    ],
  },
];

const MAX_MATCHES = 3;

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { query, people } = req.body || {};

  if (!query || typeof query !== 'string') {
    return res.status(400).json({ error: 'Missing "query" in request body.' });
  }
  if (!Array.isArray(people) || people.length === 0) {
    return res.status(400).json({ error: 'Missing "people" array in request body.' });
  }

  const lowerQuery = query.toLowerCase();
  for (const entry of KNOWN_ANSWERS) {
    if (entry.test(lowerQuery)) {
      return res.status(200).json({ matches: entry.matches });
    }
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({
      error: 'Server is missing ANTHROPIC_API_KEY. Add it in Vercel project settings under Environment Variables, then redeploy.'
    });
  }

  const rosterText = people
    .map(p => `${p.name} | ${p.title || ''} | Teams: ${(p.teams || []).join(', ')} | Go to for: ${p.go_to_for || ''}`)
    .join('\n');

  const systemPrompt = `You are a lookup assistant for Semafor's internal staff directory. A new hire will ask a plain-English question about who can help with something. Using ONLY the roster below, identify who can help.

Most questions have exactly one right answer — give just that one person. Only return more than one (never more than ${MAX_MATCHES}) when multiple people are each genuinely and independently a good answer to the SAME question (e.g. the question spans two distinct areas, or several people plausibly share that responsibility). Do not pad the list with tangentially-related people just to show more than one.

Reply with ONLY raw JSON, no markdown fences, no other text, in exactly this shape:
{"matches": [{"name": "Full Name", "reason": "one short sentence, under 20 words, specific to why THIS person fits"}]}
Each match needs its own distinct reason — never reuse the same reason text across two people.
If nobody in the roster is a reasonable fit, reply with:
{"matches": []}

Roster:
${rosterText}`;

  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 300,
        system: systemPrompt,
        messages: [{ role: 'user', content: query }],
      }),
    });

    if (!response.ok) {
      const errText = await response.text();
      return res.status(502).json({ error: `Claude API returned an error (${response.status}).`, detail: errText });
    }

    const data = await response.json();
    const rawText = (data.content && data.content[0] && data.content[0].text) || '';
    const cleaned = rawText.replace(/```json|```/g, '').trim();

    let parsed;
    try {
      parsed = JSON.parse(cleaned);
    } catch (e) {
      return res.status(502).json({ error: "Got a response back but couldn't parse it.", detail: rawText });
    }

    const matches = Array.isArray(parsed.matches)
      ? parsed.matches
          .filter(m => m && typeof m.name === 'string')
          .slice(0, MAX_MATCHES)
          .map(m => ({ name: m.name, reason: typeof m.reason === 'string' ? m.reason : '' }))
      : [];

    return res.status(200).json({ matches });
  } catch (err) {
    return res.status(500).json({ error: 'Request to Claude failed.', detail: String(err) });
  }
}
