// /api/slack-interactions.js
//
// Handles clicks on interactive elements in the bot's messages — right now,
// just the "🗑️ Delete this" button added to each DM response in
// slack-events.js. When clicked, deletes that specific message so you can
// keep your conversation with the bot clean (e.g. before a demo).
//
// This only deletes messages the bot itself posted — Slack's chat.delete
// only works on messages authored by the token calling it, so this can't
// delete your own typed questions (those you delete the normal Slack way:
// hover the message -> ... -> Delete message).
//
// ---- one-time setup ----
// 1. Deploy this file to news-data1 at api/slack-interactions.js.
// 2. In the Slack app settings, left sidebar -> "Interactivity & Shortcuts":
//    turn it on. Request URL:
//      https://news-data1.vercel.app/api/slack-interactions
// 3. Save. No new OAuth scopes needed — chat:write (already granted)
//    covers the bot deleting its own messages.

export const config = {
  api: { bodyParser: false },
};

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
  const payloadRaw = params.get('payload');
  if (!payloadRaw) {
    res.status(200).send('');
    return;
  }

  let payload;
  try {
    payload = JSON.parse(payloadRaw);
  } catch {
    res.status(200).send('');
    return;
  }

  if (payload.type === 'block_actions') {
    const action = (payload.actions || [])[0];
    if (action && action.action_id === 'delete_response') {
      const channel = payload.channel && payload.channel.id;
      const ts = payload.message && payload.message.ts;
      const token = process.env.SLACK_BOT_TOKEN;
      if (channel && ts && token) {
        try {
          await fetch('https://slack.com/api/chat.delete', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json; charset=utf-8',
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({ channel, ts }),
          });
        } catch {
          // fail silently — nothing useful to surface back through this ack
        }
      }
    }
  }

  res.status(200).send('');
}
