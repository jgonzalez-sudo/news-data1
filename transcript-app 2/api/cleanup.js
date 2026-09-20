const { del } = require('@vercel/blob');
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

    if (!body.url) {
      res.status(400).json({ error: 'Missing url' });
      return;
    }
    await del(body.url);
    res.status(200).json({ ok: true });
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message || 'Unexpected error' });
  }
};
