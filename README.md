# Flight Price Notifier

設定航線與目標價，機票降價就通知你 — set a route and a target price, get an email when the fare drops.

A static single-page app: **Vite + React 19 + React Router**, Tailwind CSS v4, TanStack Query, and Supabase for auth and data. Originally scaffolded with [Lovable](https://lovable.dev).

## Routes

| Path | Page |
| --- | --- |
| `/` | Landing page |
| `/sign-in` | Sign in (and "forgot password") |
| `/sign-up` | Create account |
| `/reset-password` | Set a new password (target of the reset email) |
| `/app` | My alerts — requires sign-in, otherwise redirects to `/sign-in` |
| `/auth` | Legacy URL, redirects to `/sign-in` |

## Development

Requires [Bun](https://bun.sh) (or Node.js 20+ with npm).

```sh
bun install
bun run dev        # http://localhost:5173
bun run test
bun run build      # static output in dist/
bun run preview    # serve dist/ locally
```

Environment variables (read by Vite at build time, see `.env`):

- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_PUBLISHABLE_KEY`

Only the publishable (anon) key belongs here — it ships to the browser. Never put a service-role key in a `VITE_*` variable.

## Deploying to Vercel

1. Import the repo in Vercel. The framework preset is **Vite**; `vercel.json` sets the build command (`vite build`) and output directory (`dist`).
2. If you remove `.env` from the repo, add the two `VITE_*` variables under **Project → Settings → Environment Variables**.
3. `vercel.json` rewrites every path that isn't a real file to `index.html`, so deep links like `/app` load the SPA and React Router takes over.
4. In Supabase **Authentication → URL Configuration**, add your Vercel domain (e.g. `https://your-app.vercel.app/**`) to the redirect URLs so sign-up confirmation and password-reset emails link back correctly.
