# MUSE website

A Next.js 16 app on Supabase. For now it has one page, **Timeline**, plus
email sign-in. Only accounts you've added in Supabase can sign in.

```
app/
  timeline/page.tsx      loads your data (Supabase RPC) and renders the Timeline
  login/page.tsx         "email me a sign-in link"
  auth/callback/route.ts turns the email link into a session
  auth/signout/route.ts
  layout.tsx, globals.css
components/timeline/     the Timeline itself: pure React, props in, page out
lib/period.ts            what "month 2024-10" or "week of 7 Oct" means (tested)
lib/supabase/            Supabase clients for server and browser
proxy.ts                 keeps the session fresh; sends signed-out visitors to /login
demo/                    builds the standalone preview from exported test data
tests/                   date logic tests
```

The page only ever reads through two SQL functions, `timeline_overview()` and
`timeline_period()` (migration `0005`). They run as the signed-in user, so
row level security keeps every visitor to their own history.

## Put it online

You need: the Supabase project from the main README (with your history
imported), a GitHub account, and a free Vercel account. About 20 minutes.

### 1. Supabase

1. In the **SQL Editor**, run `supabase/migrations/0005_timeline_functions.sql`
   (after 0001 to 0004, if you haven't run them).
2. **Authentication > Sign In / Providers**: turn off *Allow new users to sign
   up*. MUSE is private; you added yourself as a user already.
3. Note your **Project URL** and **publishable key** (Project Settings > API
   Keys; an older project may call it the *anon* key). Both are safe to put in
   a website: the key can only do what row level security allows.
   **Never** put the database connection string or the `service_role` /
   secret key in the website.

### 2. Run it on your computer first

Needs Node.js 20.9 or newer.

```bash
cd web
cp .env.example .env.local        # paste your Project URL and publishable key
npm install
npm test                          # date logic tests
npm run build                     # catches any type errors before deploying
npm run dev                       # http://localhost:3000
```

In Supabase, **Authentication > URL Configuration**: add
`http://localhost:3000/auth/callback` to *Redirect URLs*. Then sign in at
http://localhost:3000 with your email and open the link it sends you.

### 3. Push to GitHub

Create a private repository and push the whole `muse` folder. The
`.gitignore` files keep out `.env.local`, test data and Spotify exports;
check `git status` before your first commit anyway.

### 4. Deploy on Vercel

1. **Add New > Project**, and import the repository.
2. Set **Root Directory** to `web`. Vercel detects Next.js.
3. Under **Environment Variables**, add `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` with the same values as
   `.env.local`.
4. Deploy. You get an address like `https://muse-yourname.vercel.app`.

### 5. Tell Supabase about the address

**Authentication > URL Configuration**:
- *Site URL*: your Vercel address.
- *Redirect URLs*: add `https://<your address>/auth/callback`.

Sign in on your phone, then add it to your home screen if you like.

Every push to GitHub redeploys automatically. New plays appear after the next
import (`python -m muse_import import ...`); the site reads live from the
database.

### If something goes wrong

- **The sign-in link opens a "link expired" page or goes back to /login**:
  the callback address is missing from *Redirect URLs*, or the link was opened
  in a different browser from the one that asked for it.
- **"There's no MUSE account for that email"**: add the user in
  Authentication > Users.
- **The Timeline says it couldn't load**: migration 0005 hasn't been run, or
  the history import went to a different user (check `python -m muse_import
  batches`).
- **Sign-in emails stop arriving**: Supabase's built-in email sender allows only a
  few emails an hour. That's fine for one person; for more, connect your own
  email provider (Authentication > Emails > SMTP settings).

## The preview

`demo/` builds a single self-contained page with the same Timeline
components and data exported from a database, for showing the design
without a server:

```bash
python demo/export_data.py --db "postgresql://..." --email you@example.com
npm run demo        # writes demo/dist/index.html
```

Exported data is real listening history if you point it at your real
database: don't publish that page unless you mean to.

## Not tested yet

`next build` and the sign-in flow haven't been run (Next.js wasn't available
where this was written). The Timeline components, CSS, date logic and SQL
functions are tested; every app file compiles. Run `npm run build` once
before deploying; if it reports a type error, it will name the file and line.
