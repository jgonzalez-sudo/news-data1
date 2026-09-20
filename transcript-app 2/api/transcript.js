const { requireUser } = require('./_auth');

module.exports = async (req, res) => {
  if (req.method !== 'GET') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  try {
    await requireUser(req);

    const { id } = req.query || {};
    if (!id) {
      res.status(400).json({ error: 'Missing id' });
      return;
    }
    if (!process.env.ASSEMBLYAI_API_KEY) {
      res.status(500).json({ error: 'Server is not configured with ASSEMBLYAI_API_KEY' });
      return;
    }

    const r = await fetch(`https://api.assemblyai.com/v2/transcript/${encodeURIComponent(id)}`, {
      headers: { authorization: process.env.ASSEMBLYAI_API_KEY },
    });
    const data = await r.json();
    res.status(200).json(data);
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message || 'Unexpected error' });
  }
};
