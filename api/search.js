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

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({
      error: 'Server is missing ANTHROPIC_API_KEY. Add it in Vercel project settings under Environment Variables, then redeploy.'
    });
  }

  const rosterText = people
    .map(p => `${p.name} | ${p.title || ''} | Teams: ${(p.teams || []).join(', ')} | Go to for: ${p.go_to_for || ''}`)
    .join('\n');

  const systemPrompt = `You are a lookup assistant for Semafor's internal staff directory. A new hire will ask a plain-English question about who can help with something. Using ONLY the roster below, pick the single best-matching person (or up to 2, only if genuinely tied). Reply with ONLY raw JSON, no markdown fences, no other text, in exactly this shape:
{"matches": ["Full Name"], "reason": "one short sentence, under 20 words, explaining why this person fits"}
If nobody in the roster is a reasonable fit for the question, reply with:
{"matches": [], "reason": "one short sentence saying so"}

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
        max_tokens: 200,
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

    return res.status(200).json({
      matches: Array.isArray(parsed.matches) ? parsed.matches : [],
      reason: typeof parsed.reason === 'string' ? parsed.reason : '',
    });
  } catch (err) {
    return res.status(500).json({ error: 'Request to Claude failed.', detail: String(err) });
  }
}
