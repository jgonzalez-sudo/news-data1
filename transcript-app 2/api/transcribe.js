const { requireUser } = require('./_auth');

function readJson(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => { data += chunk; });
    req.on('end', () => {
      try { resolve(JSON.parse(data || '{}')); }
      catch (e) { reject(e); }
    });
    req.on('error', reject);
  });
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  try {
    await requireUser(req);
    const body = await readJson(req);

    if (!body.audioUrl) {
      res.status(400).json({ error: 'Missing audioUrl' });
      return;
    }
    if (!process.env.ASSEMBLYAI_API_KEY) {
      res.status(500).json({ error: 'Server is not configured with ASSEMBLYAI_API_KEY' });
      return;
    }

    const r = await fetch('https://api.assemblyai.com/v2/transcript', {
      method: 'POST',
      headers: {
        authorization: process.env.ASSEMBLYAI_API_KEY,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ audio_url: body.audioUrl, speaker_labels: true }),
    });

    const data = await r.json();
    if (!r.ok) {
      res.status(r.status).json({ error: data.error || 'AssemblyAI request failed' });
      return;
    }

    res.status(200).json({ id: data.id });
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message || 'Unexpected error' });
  }
};
