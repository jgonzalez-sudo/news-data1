const { handleUpload } = require('@vercel/blob/client');
const { requireUser } = require('./_auth');

function readRawBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => { data += chunk; });
    req.on('end', () => resolve(data));
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

    const raw = await readRawBody(req);
    const body = JSON.parse(raw || '{}');

    // handleUpload wants a Fetch-style Request for header introspection.
    const proto = req.headers['x-forwarded-proto'] || 'https';
    const host = req.headers['x-forwarded-host'] || req.headers.host || 'localhost';
    const request = new Request(`${proto}://${host}${req.url}`, {
      method: 'POST',
      headers: req.headers,
    });

    const jsonResponse = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async () => ({
        allowedContentTypes: ['audio/*', 'video/*'],
        addRandomSuffix: true,
        // 1 GB ceiling — adjust if your team needs bigger files
        maximumSizeInBytes: 1024 * 1024 * 1024,
      }),
    });

    res.status(200).json(jsonResponse);
  } catch (err) {
    res.status(err.status || 400).json({ error: err.message || 'Upload authorization failed' });
  }
};
