# Global Digest — the day's 43 stories, built daily — newspaper front page

A tiny, dependency-free newspaper: **15 World + 10 Kenya + 6 Business + 6 Technology + 6 Sports** — each with a high-res image (BBC 800px, Guardian 800w), one-line summary, and "Read more →" to the original publisher. Light paper theme, masthead, section rules. Regenerates every morning at 06:00 EAT on GitHub Pages — no servers.

## How it works

1. `build_site.py` (Python **stdlib only** — no pip installs) fetches
    direct RSS (no Google News — avoids `googleusercontent` placeholder logos): BBC World/Africa/Business/Sport/Science, Guardian World/Africa/Business/Tech/Sport, NYT World/Business/Tech, DW, Al Jazeera, KBC, Kenyans.co.ke, Nairobi Wire, Capital FM.
2. It dedupes by headline, ranks by source + freshness, keeps the top
    15 world + 10 Kenya + 6 business + 6 tech + 6 sports (43 total), upgrades thumbs to 800px (`_upgrade_img`), drops Google placeholders (`_is_placeholder_img`), og:image fallback.
3. It renders a single, self-contained `dist/index.html` — **light newspaper** paper `#fdfaf3`, masthead `The Global Digest`, double-rules, 3-column sections, hero lead, tab filter (All/World/Kenya/Business/Tech/Sports), `referrerpolicy="no-referrer"` images, readable serif headlines.

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

- `DIGEST_GLOBAL=15`, `DIGEST_KENYA=10`, `DIGEST_BUSINESS=6`, `DIGEST_TECH=6`, `DIGEST_SPORTS=6` — overridden via env.
- Edit `FEEDS` in `build_site.py` to add/remove sources.
- `python build_site.py --debug` builds a tiny 5+3 page for testing.

Everything fails closed: a dead feed, a missing image, or a slow site
never breaks the build.