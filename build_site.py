#!/usr/bin/env python3
"""Global Digest - daily top-stories site builder (Python stdlib only).

Fetches free RSS feeds (no API keys), dedupes, ranks, and renders a
self-contained index.html with roughly 16 major world stories + 10 top
Kenyan stories of the day. Meant to run daily from GitHub Actions; the
generated dist/ folder is what GitHub Pages publishes.

Usage:
    python build_site.py            # full build -> dist/index.html
    python build_site.py --debug    # fewer items + verbose

Everything fails closed: a dead feed is skipped, a missing image gets a
styled gradient placeholder, never a crash.
"""

import concurrent.futures as _cf
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

GLOBAL_COUNT = int(os.environ.get("DIGEST_GLOBAL", "16"))
KENYA_COUNT = int(os.environ.get("DIGEST_KENYA", "10"))
TIMEOUT = float(os.environ.get("DIGEST_TIMEOUT", "6"))
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

# feed url -> (kind, weight).  kind in {"global", "kenya"}.
FEEDS = [
    ("https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "global", 4),
    ("https://news.google.com/rss?hl=en-GB&gl=GB&ceid=GB:en", "global", 3),
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "global", 3),
    ("https://feeds.bbci.co.uk/news/world/africa/rss.xml", "global", 2),
    ("https://www.aljazeera.com/xml/rss/all.xml", "global", 2),
    ("https://news.google.com/rss?hl=en-US&gl=KE&ceid=KE:en", "kenya", 4),
    ("https://news.google.com/rss?hl=sw&gl=KE&ceid=KE:sw", "kenya", 3),
    ("https://news.google.com/rss/search?q="
     "Kenya+OR+site:nation.africa+OR+site:citizen.digital"
     "+OR+site:standardmedia.co.ke&hl=en-US&gl=KE&ceid=KE:en", "kenya", 3),
    ("https://nation.africa/rss", "kenya", 2),
    ("https://www.standardmedia.co.ke/rss", "kenya", 2),
    ("https://www.citizen.digital/rss", "kenya", 2),
]

# A small, honest source label for unique Google News sources.
def _source_of(url):
    host = (urllib.parse.urlparse(url).netloc or "").lower()
    host = host.replace("www.", "")
    known = {
        "news.google.com": "Google News",
        "bbc.com": "BBC", "bbc.co.uk": "BBC",
        "aljazeera.com": "Al Jazeera",
        "reuters.com": "Reuters", "apnews.com": "AP",
        "nation.africa": "Nation", "citizen.digital": "Citizen",
        "standardmedia.co.ke": "The Standard",
        "the-star.co.ke": "The Star", "cgtn.com": "CGTN",
        "dw.com": "DW", "cnn.com": "CNN", "nytimes.com": "NYT",
    }
    for key, label in known.items():
        if host == key or host.endswith("." + key):
            return label
    if not host:
        return "News"
    return host.split(".")[0].capitalize()


def _fetch(url, timeout=TIMEOUT, binary=False):
    """Returns bytes/str body or None (never raises)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if binary:
            return data
        return data.decode("utf-8", errors="replace")
    except Exception:
        return None


def _local(tag):
    """Strip XML namespaces (media:content -> content etc.)."""
    return tag.split("}")[-1]


def _parse_rss(body):
    """Parse an RSS/Atom body into items. Each item:
    {title, link, summary, img, pub_int, source}"""
    items = []
    try:
        root = ET.fromstring(body)
    except Exception:
        return items
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = link = summary = img = pub = ""
        for child in node.iter():
            tag = _local(child.tag)
            text = (child.text or "").strip()
            if tag == "title" and not title:
                title = text
            elif tag == "link" and not link:
                link = text
                if child.attrib.get("href"):
                    link = child.attrib["href"]
            elif tag in ("description", "summary", "content") and not summary:
                summary = re.sub(r"<[^>]+>", " ", text)
            elif tag in ("thumbnail", "content") and not img:
                if child.attrib.get("url"):
                    img = child.attrib["url"]
            elif tag == "enclosure" and child.attrib.get("url") and not img:
                if "image" in child.attrib.get("type", ""):
                    img = child.attrib["url"]
            elif tag == "pubdate":
                pub = text
        if "http" not in link:
            # Atom <link href=...> only
            at = node.findall(".//*{link}") or node.findall(".//link")
            for a in at:
                if a.attrib.get("href"):
                    link = a.attrib["href"]
                    break
        if not title or not link.startswith("http"):
            continue
        pub_int = int(dt.datetime.now().timestamp())
        if pub:
            try:
                pub_dt = email.utils.parsedate_to_datetime(pub)
                pub_int = int(pub_dt.timestamp())
            except Exception:
                pass
        items.append({"title": title, "link": link,
                      "summary": _WS.sub(" ", summary).strip(),
                      "img": img, "pub_int": pub_int,
                      "source": _source_of(link)})
    return items


def _unique(items):
    """Keep the highest-scoring copy of each story (dedupe by title)."""
    out = {}
    for it in items:
        key = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:60]
        if not key:
            continue
        prev = out.get(key)
        if prev is None or it["score"] > prev["score"]:
            out[key] = it
    return list(out.values())


def _fetch_og_image(url):
    """Best-effort og:image extraction for stories that lack one."""
    page = _fetch(url)
    if not page:
        return None
    m = re.search(
        r'<meta[^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'])'
        r'[^>]+content=["\']([^"\']+)["\']',
        page, re.I)
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+'
            r'(?:property=["\']og:image["\']|name=["\']twitter:image["\'])',
            page, re.I)
    if m:
        return html.unescape(m.group(1))
    return None


_WS = re.compile(r"\s+")
_HTML_RE = re.compile(r"<[^>]+>")


def _timeago(ts):
    secs = int(dt.datetime.now().timestamp()) - ts
    if secs < 0:
        secs = 0
    if secs < 3600:
        return f"{max(1, secs // 60)}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _shorten(text, n=170):
    text = _HTML_RE.sub(" ", text or "")
    text = _WS.sub(" ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    best = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), cut.rfind(", "))
    if best > 60:
        cut = cut[: best + 1]
    return cut.strip() + " …"


def build():
    debug = "--debug" in sys.argv
    if debug:
        global GLOBAL_COUNT, KENYA_COUNT
        GLOBAL_COUNT, KENYA_COUNT = 5, 3

    all_items = []
    for url, kind, weight in FEEDS:
        body = _fetch(url)
        if not body:
            print(f"  [skip] {url} (unreachable)")
            continue
        parsed = 0
        for i, it in enumerate(_parse_rss(body)):
            if not it["link"]:
                continue
            it["kind"] = kind
            it["score"] = weight * 100 - i * 1.5
            all_items.append(it)
            parsed += 1
        print(f"  [ok]   {url} -> {parsed} items")

    ranked = _unique(all_items)
    g = sorted([x for x in ranked if x["kind"] == "global"],
               key=lambda x: -x["score"])
    k = sorted([x for x in ranked if x["kind"] == "kenya"],
               key=lambda x: -x["score"])
    globals_list = g[:GLOBAL_COUNT]
    kenya_list = k[:KENYA_COUNT]

    # Fill in missing images (concurrently, best-effort).
    missing = [it for it in globals_list + kenya_list if not it["img"]]
    if missing:
        def _fill(it):
            it["img"] = _fetch_og_image(it["link"])
            return it, bool(it["img"])
        with _cf.ThreadPoolExecutor(max_workers=8) as pool:
            for it, got in pool.map(_fill, missing):
                print(f"  [img]   {'ok  ' if got else 'none'} {it['title'][:50]}")
    for it in globals_list + kenya_list:
        if not it["img"] or not it["img"].startswith("http"):
            it["img"] = ""

    html_out = render(globals_list, kenya_list, debug)
    os.makedirs(os.path.join(_DIST, "data"), exist_ok=True)
    with open(os.path.join(_DIST, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    with open(os.path.join(_DIST, "data", "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "global": globals_list, "kenya": kenya_list}, f,
                  ensure_ascii=False, indent=2)
    print(f"\nBuilt {_DIST}/index.html with {len(globals_list)} global + "
          f"{len(kenya_list)} Kenya stories.")


_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
_CSS = """
    :root {
      --bg: #0b0e14; --bg2: #12161f; --card: #151a25;
      --ink: #e9e6e1; --muted: #9aa3b2; --gold: #d4af37;
      --gold2: #e8c96a; --line: rgba(255,255,255,.07);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html { scroll-behavior: smooth; }
    body {
      background:
        radial-gradient(1200px 600px at 85% -10%, rgba(212,175,55,.07), transparent 60%),
        radial-gradient(900px 500px at -10% 0%, rgba(64,108,196,.08), transparent 55%),
        var(--bg);
      color: var(--ink);
      font-family: Georgia, 'Times New Roman', serif;
      line-height: 1.55;
      min-height: 100vh;
    }
    .wrap { max-width: 1180px; margin: 0 auto; padding: 0 22px; }
    header.mast {
      padding: 34px 0 18px; border-bottom: 1px solid var(--line);
      display: flex; flex-wrap: wrap; align-items: baseline; gap: 14px;
    }
    .brand { font-size: 30px; font-weight: 700; letter-spacing: .5px;
             color: var(--ink); }
    .brand .dot { color: var(--gold); }
    .tagline { color: var(--muted); font-size: 13px; font-style: italic; }
    .pill { margin-left: auto; font-size: 12px; letter-spacing: .12em;
            text-transform: uppercase; color: var(--gold);
            border: 1px solid rgba(212,175,55,.45); padding: 6px 12px;
            border-radius: 999px; white-space: nowrap; }
    .datebar { color: var(--muted); font-size: 13px; padding: 14px 0 26px;
               display: flex; gap: 16px; flex-wrap: wrap; }
    .datebar b { color: var(--ink); font-weight: 600; }
    .section { display: flex; align-items: center; gap: 14px;
               margin: 40px 0 18px; }
    .section .rule { flex: 1; height: 1px; background: linear-gradient(
                     90deg, rgba(212,175,55,.5), transparent); }
    .section h2 { font-size: 20px; letter-spacing: .08em;
                  text-transform: uppercase; color: var(--gold2); }
    /* hero */
    .hero { display: grid; grid-template-columns: 7fr 5fr; gap: 22px;
            background: var(--card); border: 1px solid var(--line);
            border-radius: 18px; overflow: hidden; }
    .hero img { width: 100%; height: 100%; min-height: 300px; object-fit: cover; }
    .hero .hbody { padding: 26px 26px 24px; display: flex;
                   flex-direction: column; gap: 12px; }
    .badge { align-self: flex-start; font-size: 11px; letter-spacing: .14em;
             text-transform: uppercase; color: #0b0e14; background: var(--gold);
             border-radius: 999px; padding: 4px 11px; font-family: inherit; }
    .hero h1 { font-size: clamp(22px, 3vw, 32px); line-height: 1.25; }
    .hero p.sum { color: var(--muted); }
    .meta { color: var(--muted); font-size: 12px; }
    .readmore { color: var(--gold); text-decoration: none; font-weight: 600;
                font-size: 14px; margin-top: auto; letter-spacing: .02em; }
    .readmore:hover { color: var(--gold2); text-decoration: underline; }
    /* grid */
    .grid { display: grid; grid-template-columns: repeat(auto-fill,
            minmax(330px, 1fr)); gap: 22px; }
    .card { background: var(--card); border: 1px solid var(--line);
            border-radius: 15px; overflow: hidden;
            display: flex; flex-direction: column;
            transition: transform .18s ease, border-color .18s ease; }
    .card:hover { transform: translateY(-3px); border-color: var(--gold); }
    .thumb { width: 100%; aspect-ratio: 16 / 10; object-fit: cover;
             background: linear-gradient(135deg, #1c2333, #2a1f12);
             display: block; }
    .cbody { padding: 16px 18px 18px; display: flex;
             flex-direction: column; gap: 8px; flex: 1; }
    .cbody h3 a { color: var(--ink); text-decoration: none; font-size: 17px;
                  line-height: 1.35; }
    .cbody h3 a:hover { color: var(--gold2); }
    .card .sum { color: var(--muted); font-size: 13px; }
    .card .meta { margin-top: auto; padding-top: 6px; }
    .srcpill { font-size: 10px; letter-spacing: .12em; text-transform: uppercase;
               color: var(--gold); border: 1px solid rgba(212,175,55,.35);
               padding: 2px 9px; border-radius: 999px; align-self: flex-start; }
    footer { margin: 54px 0 40px; padding-top: 20px; border-top: 1px solid var(--line);
             color: var(--muted); font-size: 12px; display: flex;
             flex-wrap: wrap; gap: 10px 22px; align-items: center; }
    footer b { color: var(--ink); }
    @media (max-width: 820px) {
      .hero { grid-template-columns: 1fr; }
      .hero img { min-height: 210px; }
      .pill { margin-left: 0; }
    }
"""


def _card(it, idx):
    img = (f'<img loading="lazy" class="thumb" src="{html.escape(it["img"])}" '
           f'alt="{html.escape(it["title"])}">'
           if it["img"] else '<div class="thumb"></div>')
    return f"""<article class="card">
      {img}
      <div class="cbody">
        <span class="srcpill">{html.escape(it["source"])}</span>
        <h3><a href="{html.escape(it["link"], quote=True)}" target="_blank" rel="noopener">{html.escape(it["title"])}</a></h3>
        <p class="sum">{html.escape(_shorten(it["summary"]))}</p>
        <p class="meta">{_timeago(it["pub_int"])} · <a class="readmore" href="{html.escape(it["link"], quote=True)}" target="_blank" rel="noopener">Read more →</a></p>
      </div>
    </article>"""


def render(globals_list, kenya_list, debug=False):
    today = dt.date.today()
    today_label = (f"{today.strftime('%A')}, {today.day} "
                   f"{today.strftime('%B')} {today.year}")
    e = html.escape
    hero = globals_list[0] if globals_list else None
    cards_g = "".join(_card(it, i) for i, it in enumerate(globals_list[1:]))
    cards_k = "".join(_card(it, i) for i, it in enumerate(kenya_list))
    hero_html = ""
    if hero:
        hbody = (f'<img src="{e(hero["img"])}" alt="{e(hero["title"])}">'
                 if hero["img"] else '<img alt="" style="display:none">')
        hero_html = f"""<a href="{e(hero['link'], True)}" target="_blank" rel="noopener" class="hero">
        {hbody}
        <div class="hbody">
          <span class="badge">Top story</span>
          <h1>{e(hero['title'])}</h1>
          <p class="sum">{e(_shorten(hero['summary'], 260))}</p>
          <p class="meta">{hero['source']} · {_timeago(hero['pub_int'])}</p>
          <span class="readmore">Read the full story →</span>
        </div>
      </a>"""
    updated = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).strftime("%H:%M EAT")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Global Digest — Today's Top Stories</title>
<meta name="description" content="The ~20 biggest stories of the day - world and Kenya - refreshed every morning.">
<meta property="og:title" content="Global Digest — Today's Top Stories">
<meta property="og:description" content="The ~20 biggest stories of the day - world and Kenya - refreshed every morning.">
<meta property="og:type" content="website">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📰</text></svg>">
<style>{_CSS}</style>
</head>
<body>
  <div class="wrap">
    <header class="mast">
      <span class="brand">Global<span class="dot">·</span>Digest</span>
      <span class="tagline">the day's major stories, carefully picked</span>
      <span class="pill">Updated today · {updated}</span>
    </header>
    <div class="datebar"><span><b>{today_label}</b></span>
      <span>{len(globals_list)} world + {len(kenya_list)} Kenya stories</span></div>

    {hero_html}

    <div class="section"><h2>World</h2><div class="rule"></div></div>
    <section class="grid">{cards_g}</section>

    <div class="section"><h2>Kenya</h2><div class="rule"></div></div>
    <section class="grid">{cards_k}</section>

    <footer>
      <span><b>Global Digest</b> — a daily top-stories page, built automatically.</span>
      <span>Sources: Google News, BBC, Al Jazeera, Nation, Citizen, The Standard.</span>
      <span>“Read more” opens the original article.</span>
      <span>No middleware, no tracking.</span>
    </footer>
  </div>
</body>
</html>
"""


if __name__ == "__main__":
    build()