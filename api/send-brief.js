export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const webhookUrl = process.env.SLACK_WEBHOOK_URL;
  if (!webhookUrl) {
    return res.status(500).json({ error: 'SLACK_WEBHOOK_URL not configured' });
  }

  const brief = req.body || {};
  const cb = brief.continental_briefing || {};

  const bullets = (items) =>
    (items && items.length) ? items.map((i) => `• ${i}`).join('\n') : '_none flagged_';

  const execBullets = (items) =>
    (items && items.length)
      ? items.map((i) => (i && i.url ? `• <${i.url}|${i.headline || 'Untitled'}>` : `• ${(i && i.headline) || i}`)).join('\n')
      : '_none flagged_';

  const blocks = [
    { type: 'header', text: { type: 'plain_text', text: 'Africa daily brief (on demand)' } },
    { type: 'section', text: { type: 'mrkdwn', text: '*Today\'s biggest stories:*\n' + execBullets(brief.executive_summary) } },
    { type: 'divider' },
    { type: 'section', text: { type: 'mrkdwn', text: '*Business & macro*\n' + bullets(cb.business_macro) } },
    { type: 'section', text: { type: 'mrkdwn', text: '*Climate & energy*\n' + bullets(cb.climate_energy) } },
    { type: 'section', text: { type: 'mrkdwn', text: '*Geopolitics & politics*\n' + bullets(cb.geopolitics_politics) } },
    { type: 'section', text: { type: 'mrkdwn', text: '*Tech & deals*\n' + bullets(cb.tech_deals) } },
    { type: 'divider' },
    { type: 'section', text: { type: 'mrkdwn', text: '*Weekend read*\n' + (brief.weekend_read_suggestion || '_none flagged_') } },
  ];

  try {
    const slackRes = await fetch(webhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ blocks }),
    });
    if (!slackRes.ok) {
      return res.status(502).json({ error: 'Slack rejected the message' });
    }
    return res.status(200).json({ ok: true });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
