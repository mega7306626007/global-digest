# Global Digest — the day's ~20 major stories, built daily

A tiny, dependency-free news page: **16 major world stories + 10 top
Kenyan stories**, each with a picture, a one-line summary, and a
"Read more →" link to the original article. It regenerates itself
every morning at 06:00 EAT and is served free forever from GitHub
Pages — no servers, so it never spins down.

## How it works

1. `build_site.py` (Python **stdlib only** — no pip installs) fetches
   free RSS feeds: Google News (US, UK, Kenya, Kiswahili), BBC World,
   BBC Africa, Al Jazeera, Nation, Citizen, The Standard.
2. It dedupes by headline, ranks by source + freshness, keeps the top
   16 world + 10 Kenyan stories, and pulls each story's lead image
   (og:image fallback when a feed omits one; styled placeholder if
   nothing is found).
3. It renders a single, self-contained `dist/index.html` — dark
   editorial theme, hero card for the #1 story, responsive card grid,
   date header, "Xh ago" stamps.

## Daily automation

`.github/workflows/daily.yml` runs the build on a cron schedule and
publishes `dist/` to the `gh-pages` branch (via
`peaceiris/actions-gh-pages`). GitHub Pages then serves it at:

    https://<your-username>.github.io/global-digest/

You can also trigger a rebuild manually from the **Actions** tab.

## One-time setup

1. Create the repo: `gh repo create global-digest --public`
   (or create it on github.com).
2. `git init`, `git add .`, `git commit`, `git push -u origin main`.
3. On the repo page: **Settings → Pages → Source: Deploy from a
   branch → branch: `gh-pages` / root**. (The first daily build, or a
   manual "Run workflow" run, creates that branch.)
4. Open the Pages URL — done. It updates itself after that.

## Local preview

    python build_site.py
    start dist\index.html        # Windows
    python -m http.server -d dist  # or a local server

## Config

- `DIGEST_GLOBAL=16`, `DIGEST_KENYA=10` — counts can be overridden via
  environment variables.
- Edit `FEEDS` in `build_site.py` to add/remove sources.
- `python build_site.py --debug` builds a tiny 5+3 page for testing.

Everything fails closed: a dead feed, a missing image, or a slow site
never breaks the build.