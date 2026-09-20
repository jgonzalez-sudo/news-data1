# Transcript

A small internal tool: upload or record audio, get a speaker-labeled transcript back (via AssemblyAI). Colleagues sign in with their work Google account — nobody handles an API key. Runs on Vercel.

## How it works

- Colleagues sign in with **Google** (via Google's own "Sign in with Google" library — no Firebase, no separate user database). Their sign-in token is sent with every request and verified on the server.
- The browser uploads the audio file **directly to Vercel Blob storage** (not through our server) using a short-lived token our backend hands out only to signed-in users. This sidesteps Vercel's 4.5 MB request-size limit on serverless functions — real recordings would blow past that instantly if we tried to relay the bytes ourselves.
- Our backend holds the **one shared AssemblyAI key** and does two small things: tells AssemblyAI "fetch the file from this Blob URL and transcribe it," and relays status when the frontend polls.
- Optionally, you can restrict sign-in to your company's email domain so a stranger with the link can't use your AssemblyAI account.

## Deploy it

### 1. Set up Google Sign-In

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a project (or use an existing one).
2. **APIs & Services → OAuth consent screen** — set it up (Internal if you're on Google Workspace and only want your org to see it; External + "Testing" mode otherwise while you try this out).
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID** → Application type: **Web application**.
4. Under **Authorized JavaScript origins**, add the URL you'll deploy to (you can add `http://localhost:3000` now for local testing and add the real `https://your-app.vercel.app` URL after step 3 below — you can always come back and add more origins later).
5. Copy the **Client ID** it gives you (looks like `123456-abc.apps.googleusercontent.com`). You'll use this exact value in two places below.

### 2. Get the code onto GitHub

Unzip this project, `git init`, commit, and push to a new GitHub repo.

### 3. Import into Vercel

On vercel.com, "Add New… → Project" → import that repo. Leave build settings as-is (there's no build step). Deploy once to get your `*.vercel.app` URL, then go back to Google Cloud Console and add that exact URL to **Authorized JavaScript origins** (step 1.4 above).

### 4. Add a Blob store

In the Vercel project: **Storage** tab → **Create Database** → **Blob** → create it and connect it to this project. Vercel automatically sets a `BLOB_READ_WRITE_TOKEN` environment variable — you don't need to touch it.

### 5. Paste your Google Client ID into the frontend

Open `public/index.html`, find this line near the top of the `<script>` block:

```js
const GOOGLE_CLIENT_ID = 'YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com';
```

Replace it with your real Client ID from step 1, commit, and push (Vercel redeploys automatically on push). This value isn't secret — it's fine for it to be visible in the page source.

### 6. Set environment variables

Project → Settings → Environment Variables:

- `ASSEMBLYAI_API_KEY` — from your AssemblyAI dashboard
- `GOOGLE_CLIENT_ID` — the **same** Client ID you pasted into `index.html`
- `ALLOWED_EMAIL_DOMAIN` — (recommended) your company's email domain, e.g. `acme.com`. Without this, *any* Google account can sign in and use your AssemblyAI key.

Redeploy after adding these (Vercel will prompt you, or push an empty commit).

### 7. Send colleagues the link

They open it, click "Sign in with Google," and they're in. Nothing to install, no key to find.

### 8. Getting it on their phones

This is a Progressive Web App — it can be "installed" straight from the browser with a real home-screen icon and no browser address bar, without going through the App Store or Play Store (no developer account, no app review, no waiting). Send colleagues these instructions along with the link:

- **iPhone (Safari):** open the link → tap the Share icon (square with an arrow) → **Add to Home Screen**.
- **Android (Chrome):** open the link → Chrome will usually show an **Install app** banner automatically; if not, tap the **⋮** menu → **Install app** (or **Add to Home Screen**).

Once installed, it opens full-screen with its own icon, like any other app — it's still the same website underneath, just without the address bar.

A real app-store app (searchable in the App Store / Play Store) is also possible, but it's a materially bigger undertaking: it means wrapping this in a native shell (e.g. via Capacitor), paying for an Apple Developer account ($99/year) and a Google Play Developer account ($25 one-time), and going through Apple's app review process. Given this is an internal newsroom tool, the installable-web-app route above gets you the same "icon on your phone" experience for effectively zero extra cost or waiting — worth revisiting the app-store route only if you specifically need it discoverable by search or need capabilities a web app can't provide.

## A few things worth knowing

- **Sessions expire after about an hour** (that's how long a Google sign-in token lasts). If someone's mid-transcription and it expires, they'll be asked to sign in again — their transcripts already in progress aren't lost, but a very long-running job might need a retry. For typical recordings this won't come up.
- **Audio URLs are public-but-unguessable.** AssemblyAI needs to fetch the file from a URL it can reach, so uploads go to a public Blob URL with a long random suffix — not indexed anywhere, but not access-controlled either. There's a **Delete audio** button (trash icon, top right) that removes the file from storage once you're happy with the transcript — the text stays, only the audio goes.
- **Without `ALLOWED_EMAIL_DOMAIN` set, anyone with a Google account can sign in** and use your AssemblyAI key if they find the URL. Setting it is a one-line change and strongly recommended for a company deployment.
- **Cost.** Everything runs through your AssemblyAI account, billed by audio duration — check your AssemblyAI dashboard for current rates. Vercel Blob storage and function invocations have their own usage-based costs, but at this scale they'll likely stay in Vercel's free tier.
- **I haven't been able to test this against real Google/Vercel/AssemblyAI deployments end-to-end** — built against current documentation for all three, but I don't have live access to any of them from where I'm working. Send me whatever error you hit and I'll help sort it out.

## Local development

You'll need the [Vercel CLI](https://vercel.com/docs/cli): `npm i -g vercel`, then from this folder:

```
vercel link      # connect to your Vercel project
vercel env pull  # pulls ASSEMBLYAI_API_KEY, GOOGLE_CLIENT_ID, ALLOWED_EMAIL_DOMAIN, BLOB_READ_WRITE_TOKEN into .env.local
vercel dev       # runs the site + functions locally
```

Remember to add `http://localhost:3000` (or whatever port `vercel dev` uses) to the Google OAuth client's Authorized JavaScript origins for sign-in to work locally.
