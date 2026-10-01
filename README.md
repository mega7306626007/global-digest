# Global Digest

<p align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/GitHub_Actions-2088FF?style=for-the-badge&logo=githubactions&logoColor=white" />
  <img src="https://img.shields.io/badge/GitHub_Pages-238636?style=for-the-badge" />
  <img src="https://img.shields.io/badge/150_Stories-Daily-ff5e6c?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Zero-Dependencies-238636?style=for-the-badge" />
</p>

> **The day's 150 stories, built daily — a newspaper front page with no newsroom.**

A tiny, dependency-free newspaper: **30 World + 20 Kenya + 20 Business + 20 Technology + 20 Sports + 20 Health + 20 Culture (150 total)** — each with a high-res image, one-line summary, and "Read more" to the original publisher. Regenerates daily at 03:50 EAT on GitHub Pages.

## How it works

1. `build_site.py` (Python **stdlib only** — no pip installs) fetches direct RSS from BBC, Guardian, NYT, DW, Al Jazeera, KBC, Kenyans.co.ke, Nairobi Wire, Capital FM.
2. Dedupes by headline, ranks by source + freshness (48h bonus), keeps top 150, upgrades BBC thumbs to 800px, drops Google placeholders.
3. Renders a single, self-contained `dist/index.html` — light newspaper + dark mode, masthead, ticker, search, tabs, hero lead, 3-column grids, sidebar, print-ready.

## Daily automation

`.github/workflows/daily.yml` runs the build on a cron schedule and publishes `dist/` to the `gh-pages` branch. GitHub Pages serves it at:

    https://<your-username>.github.io/global-digest/

## One-time setup

1. Create the repo: `gh repo create global-digest --public`
2. `git init`, `git add .`, `git commit`, `git push -u origin main`
3. Settings -> Pages -> Source: Deploy from a branch -> `gh-pages` / root
4. Open the Pages URL — done. It updates itself after that.

## Local preview

```bash
python build_site.py
python -m http.server -d dist
```

## Config

- `DIGEST_GLOBAL=30`, `DIGEST_KENYA=20`, `DIGEST_BUSINESS=20`, `DIGEST_TECH=20`, `DIGEST_SPORTS=20`, `DIGEST_HEALTH=20`, `DIGEST_CULTURE=20` (150 total)
- Edit `FEEDS` in `build_site.py` to add/remove sources
- `python build_site.py --debug` builds a tiny 5+3 page for testing

Everything fails closed: a dead feed, a missing image, or a slow site never breaks the build.