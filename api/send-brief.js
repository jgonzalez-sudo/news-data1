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

  const linkedBullets = (items) =>
    (items && items.length)
      ? items.map((i) => (i && i.url ? `• <${i.url}|${i.text || 'Untitled'}>` : `• ${(i && i.text) || i}`)).join('\n')
      : '_none flagged_';

  const execBullets = (items) =>
    (items && items.length)
      ? items.map((i) => (i && i.url ? `• <${i.url}|${i.headline || 'Untitled'}>` : `• ${(i && i.headline) || i}`)).join('\n')
      : '_none flagged_';

  const weekendRead = (item) => {
    if (!item) return '_none flagged_';
    return item.url ? `<${item.url}|${item.text || 'Untitled'}>` : (item.text || item);
  };

  const blocks = [
    { type: 'header', text: { type: 'plain_text', text: 'Africa daily brief (on demand)' } },
    { type: 'section', text: { type: 'mrkdwn', text: '*Today\'s biggest stories:*\n' + execBullets(brief.executive_summary) } },
    { type: 'divider' },
    { type: 'section', text: { type: 'mrkdwn', text: '*Business & macro*\n' + linkedBullets(cb.business_macro) } },
    { type: 'section', text: { type: 'mrkdwn', text: '*Climate & energy*\n' + linkedBullets(cb.climate_energy) } },
    { type: 'section', text: { type: 'mrkdwn', text: '*Geopolitics & politics*\n' + linkedBullets(cb.geopolitics_politics) } },
    { type: 'section', text: { type: 'mrkdwn', text: '*Tech & deals*\n' + linkedBullets(cb.tech_deals) } },
    { type: 'divider' },
    { type: 'section', text: { type: 'mrkdwn', text: '*Weekend read*\n' + weekendRead(brief.weekend_read_suggestion) } },
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
