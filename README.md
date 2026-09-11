# Global Digest — the day's 150 stories, built daily — newspaper front page

A tiny, dependency-free newspaper: **30 World + 20 Kenya + 20 Business + 20 Technology + 20 Sports + 20 Health + 20 Culture (150 total)** — each with a high-res image (BBC 800px, Guardian width), one-line summary, and "Read more →" to the original publisher. Light paper `#fdfaf3`, masthead, ticker, search, dark/light, bookmarks. Regenerates daily at 03:50 EAT (auto) on GitHub Pages — no servers.

## How it works

1. `build_site.py` (Python **stdlib only** — no pip installs) fetches
    direct RSS (no Google News — avoids `googleusercontent` placeholder logos): BBC World/Africa/Business/Sport/Science, Guardian World/Africa/Business/Tech/Sport, NYT World/Business/Tech, DW, Al Jazeera, KBC, Kenyans.co.ke, Nairobi Wire, Capital FM.
2. It dedupes by headline, ranks by source + freshness (48h bonus), keeps the top
    30 world + 20 Kenya + 20 business + 20 tech + 20 sports + 20 health + 20 culture (150 total), upgrades BBC thumbs to 800px (`_upgrade_img` keeps Guardian signature), drops Google placeholders (`_is_placeholder_img`), `referrerpolicy="no-referrer"` + `onerror` fallback.
3. It renders a single, self-contained `dist/index.html` — **light newspaper** paper `#fdfaf3` + dark `data-theme`, masthead, ticker, toolbar with search + `All 150` tabs, hero lead, 3-column grids, `layout` with `Trending`/`Saved`/`Newsletter` sidebar, print-ready.

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

- `DIGEST_GLOBAL=30`, `DIGEST_KENYA=20`, `DIGEST_BUSINESS=20`, `DIGEST_TECH=20`, `DIGEST_SPORTS=20`, `DIGEST_HEALTH=20`, `DIGEST_CULTURE=20` — overridden via env (`150` total).
- Edit `FEEDS` in `build_site.py` to add/remove sources.
- `python build_site.py --debug` builds a tiny 5+3 page for testing.

Everything fails closed: a dead feed, a missing image, or a slow site
never breaks the build.