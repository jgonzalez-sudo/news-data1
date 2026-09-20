const { OAuth2Client } = require('google-auth-library');

let client;
function getClient() {
  if (!client) client = new OAuth2Client(process.env.GOOGLE_CLIENT_ID);
  return client;
}

/**
 * Verifies the Google ID token on an incoming request.
 * Throws an Error with a `.status` code on any failure.
 * Returns { email, name } on success.
 */
async function requireUser(req) {
  const authHeader = req.headers['authorization'] || '';
  const match = authHeader.match(/^Bearer\s+(.+)$/i);
  if (!match) {
    const e = new Error('Missing sign-in token');
    e.status = 401;
    throw e;
  }

  if (!process.env.GOOGLE_CLIENT_ID) {
    const e = new Error('Server is not configured with GOOGLE_CLIENT_ID');
    e.status = 500;
    throw e;
  }

  let ticket;
  try {
    ticket = await getClient().verifyIdToken({
      idToken: match[1],
      audience: process.env.GOOGLE_CLIENT_ID,
    });
  } catch (err) {
    const e = new Error('Your sign-in has expired — please sign in again');
    e.status = 401;
    throw e;
  }

  const payload = ticket.getPayload();
  if (!payload || !payload.email_verified) {
    const e = new Error('Google account email is not verified');
    e.status = 401;
    throw e;
  }

  const allowedDomain = process.env.ALLOWED_EMAIL_DOMAIN;
  if (allowedDomain) {
    const domain = (payload.email || '').split('@')[1];
    if (domain !== allowedDomain) {
      const e = new Error(`This app is restricted to @${allowedDomain} accounts`);
      e.status = 403;
      throw e;
    }
  }

  return { email: payload.email, name: payload.name };
}

module.exports = { requireUser };
