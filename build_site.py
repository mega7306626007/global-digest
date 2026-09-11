#!/usr/bin/env python3
"""Global Digest - daily top-stories site builder (Python stdlib only).

Fetches free RSS feeds (no API keys), dedupes, ranks, and renders a
self-contained newspaper-style index.html with 30 world + 20 Kenya + 20 business
+ 20 tech + 20 sports + 20 health + 20 culture stories (150 daily). Meant to run daily from GitHub Actions; the
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

GLOBAL_COUNT = int(os.environ.get("DIGEST_GLOBAL", "30"))
KENYA_COUNT = int(os.environ.get("DIGEST_KENYA", "20"))
BUSINESS_COUNT = int(os.environ.get("DIGEST_BUSINESS", "20"))
TECH_COUNT = int(os.environ.get("DIGEST_TECH", "20"))
SPORTS_COUNT = int(os.environ.get("DIGEST_SPORTS", "20"))
HEALTH_COUNT = int(os.environ.get("DIGEST_HEALTH", "20"))
CULTURE_COUNT = int(os.environ.get("DIGEST_CULTURE", "20"))
# Display total 85
TIMEOUT = float(os.environ.get("DIGEST_TIMEOUT", "6"))
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

# feed url -> (kind, weight).
# Direct RSS only — Google News CBM links return 400 and show Google logos.
FEEDS = [
    # World
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "global", 4),
    ("https://www.theguardian.com/world/rss", "global", 3),
    ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "global", 3),
    ("https://rss.dw.com/rdf/rss-en-all", "global", 2),
    ("https://feeds.bbci.co.uk/news/world/africa/rss.xml", "global", 2),
    ("https://www.aljazeera.com/xml/rss/all.xml", "global", 2),
    ("https://www.theguardian.com/world/africa/rss", "global", 2),
    # Kenya
    ("https://www.kbc.co.ke/feed/", "kenya", 4),
    ("https://www.kenyans.co.ke/rss.xml", "kenya", 3),
    ("https://nairobiwire.com/feed", "kenya", 3),
    ("https://www.capitalfm.co.ke/news/rss", "kenya", 2),
    # Business
    ("https://www.theguardian.com/business/rss", "business", 3),
    ("https://feeds.bbci.co.uk/news/business/rss.xml", "business", 3),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "business", 2),
    # Technology
    ("https://www.theguardian.com/technology/rss", "technology", 3),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "technology", 2),
    ("https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "technology", 2),
    # Sports
    ("https://www.theguardian.com/sport/rss", "sports", 3),
    ("https://feeds.bbci.co.uk/sport/rss.xml", "sports", 3),
    # Health & Culture (more topics)
    ("https://feeds.bbci.co.uk/news/health/rss.xml", "health", 3),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Health.xml", "health", 2),
    ("https://www.theguardian.com/lifeandstyle/rss", "culture", 3),
    ("https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml", "culture", 2),
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
        "theguardian.com": "The Guardian", "guardian.com": "The Guardian",
        "nytimes.com": "NYT",
        "dw.com": "DW", "cnn.com": "CNN",
        "nation.africa": "Nation", "citizen.digital": "Citizen",
        "standardmedia.co.ke": "The Standard",
        "the-star.co.ke": "The Star", "cgtn.com": "CGTN",
        "kbc.co.ke": "KBC", "capitalfm.co.ke": "Capital FM",
        "kenyans.co.ke": "Kenyans.co.ke", "nairobiwire.com": "Nairobi Wire",
    }
    for key, label in known.items():
        if host == key or host.endswith("." + key):
            return label
    if not host:
        return "News"
    return host.split(".")[0].capitalize()


def _fetch(url, timeout=TIMEOUT, binary=False):
    """Returns bytes/str body or None (never raises). Cache-busted for fresh news."""
    try:
        # Bust caches for daily freshness
        sep = "&" if "?" in url else "?"
        busted = f"{url}{sep}_={int(dt.datetime.now().timestamp())}"
        req = urllib.request.Request(busted, headers={"User-Agent": USER_AGENT,
                                                      "Accept": "*/*",
                                                      "Cache-Control": "no-cache",
                                                      "Pragma": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if binary:
            return data
        return data.decode("utf-8", errors="replace")
    except Exception:
        # fallback without busting
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            return data if binary else data.decode("utf-8", errors="replace")
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
            elif tag.lower() in ("pubdate", "published", "updated", "date"):
                if not pub:
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
                if pub_dt is None:
                    raise ValueError
                pub_int = int(pub_dt.timestamp())
            except Exception:
                try:
                    # Atom ISO8601 like 2026-09-11T00:46:00Z
                    iso = pub.strip().replace("Z", "+00:00")
                    pub_dt = dt.datetime.fromisoformat(iso)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=dt.timezone.utc)
                    pub_int = int(pub_dt.timestamp())
                except Exception:
                    pass
        items.append({"title": title, "link": link,
                       "summary": _WS.sub(" ", summary).strip(),
                       "img": _upgrade_img(img), "pub_int": pub_int,
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


def _is_placeholder_img(u):
    if not u:
        return True
    lu = u.lower()
    if "googleusercontent" in lu or "gstatic" in lu or "googlelogo" in lu:
        return True
    if "news.google.com" in lu:
        return True
    return False

def _upgrade_img(u):
    """Upgrade tiny RSS thumbs to readable size (BBC 80/240 -> 800). Guardian keep original (signature)."""
    if not u or _is_placeholder_img(u):
        return ""
    if "ichef.bbci.co.uk" in u:
        # BBC uses /80/ /240/ etc — upgrade to 800 for crisp print
        u = re.sub(r"/\d+x\d+/", "/800/", u)
        u = re.sub(r"/\d+/", "/800/", u)
    # Don't touch i.guim.co.uk — width change breaks &s= signature (401)
    return u

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
        return _upgrade_img(html.unescape(m.group(1)))
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


def _shorten(text, n=260):
    text = _HTML_RE.sub(" ", text or "")
    text = _WS.sub(" ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    best = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), cut.rfind(", "))
    if best > 60:
        cut = cut[: best + 1]
    return cut.strip() + " …"

def _reading_time(text):
    words = len((text or "").split())
    return max(1, (words + 199) // 200)  # 200 wpm

def _share_url(url, title):
    q = urllib.parse.quote
    return {
        "x": f"https://twitter.com/intent/tweet?text={q(title)}&url={q(url)}",
        "wa": f"https://wa.me/?text={q(title + ' ' + url)}",
        "copy": url,
    }


def build():
    debug = "--debug" in sys.argv
    if debug:
        global GLOBAL_COUNT, KENYA_COUNT, BUSINESS_COUNT, TECH_COUNT, SPORTS_COUNT, HEALTH_COUNT, CULTURE_COUNT
        GLOBAL_COUNT, KENYA_COUNT, BUSINESS_COUNT, TECH_COUNT, SPORTS_COUNT, HEALTH_COUNT, CULTURE_COUNT = 5, 3, 2, 2, 2, 2, 2

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
            # Filter Google placeholder images immediately
            if _is_placeholder_img(it["img"]):
                it["img"] = ""
            it["kind"] = kind
            it["score"] = weight * 100 - i * 1.5
            all_items.append(it)
            parsed += 1
        print(f"  [ok]   {url} -> {parsed} items")

    ranked = _unique(all_items)
    # Freshness bonus: newer stories rank higher (48h window)
    now = int(dt.datetime.now().timestamp())
    def _score_with_freshness(lst):
        out=[]
        for x in lst:
            age_h = (now - x["pub_int"]) / 3600
            freshness = max(0, 48 - age_h) * 0.8  # up to +38 for just-published
            x["_final"] = x["score"] + freshness
            out.append(x)
        return sorted(out, key=lambda y: -y["_final"])
    g = _score_with_freshness([x for x in ranked if x["kind"] == "global"])
    k = _score_with_freshness([x for x in ranked if x["kind"] == "kenya"])
    b = _score_with_freshness([x for x in ranked if x["kind"] == "business"])
    t = _score_with_freshness([x for x in ranked if x["kind"] == "technology"])
    s = _score_with_freshness([x for x in ranked if x["kind"] == "sports"])
    h = _score_with_freshness([x for x in ranked if x["kind"] == "health"])
    c = _score_with_freshness([x for x in ranked if x["kind"] == "culture"])
    globals_list = g[:GLOBAL_COUNT]
    kenya_list = k[:KENYA_COUNT]
    business_list = b[:BUSINESS_COUNT]
    tech_list = t[:TECH_COUNT]
    sports_list = s[:SPORTS_COUNT]
    health_list = h[:HEALTH_COUNT]
    culture_list = c[:CULTURE_COUNT]

    # Fill in missing / Google placeholder images (concurrently, best-effort).
    all_selected = globals_list + kenya_list + business_list + tech_list + sports_list + health_list + culture_list
    missing = [it for it in all_selected if not it["img"] or _is_placeholder_img(it["img"])]
    if missing:
        def _fill(it):
            img = _fetch_og_image(it["link"])
            if img and not _is_placeholder_img(img):
                it["img"] = img
            else:
                it["img"] = it["img"] if it["img"] and not _is_placeholder_img(it["img"]) else ""
            return it, bool(it["img"])
        with _cf.ThreadPoolExecutor(max_workers=10) as pool:
            for it, got in pool.map(_fill, missing):
                print(f"  [img]   {'ok  ' if got else 'none'} {it['title'][:50]}")
    for it in all_selected:
        if not it["img"] or not it["img"].startswith("http") or _is_placeholder_img(it["img"]):
            it["img"] = ""

    # Ensure BBC thumbnails are upgraded even if from cache
    for it in all_selected:
        it["img"] = _upgrade_img(it["img"])

    html_out = render(globals_list, kenya_list, business_list, tech_list, sports_list, health_list, culture_list, debug)
    os.makedirs(os.path.join(_DIST, "data"), exist_ok=True)
    with open(os.path.join(_DIST, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    with open(os.path.join(_DIST, "data", "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "global": globals_list, "kenya": kenya_list,
                   "business": business_list, "technology": tech_list, "sports": sports_list,
                   "health": health_list, "culture": culture_list}, f,
                  ensure_ascii=False, indent=2)
    total = len(globals_list)+len(kenya_list)+len(business_list)+len(tech_list)+len(sports_list)+len(health_list)+len(culture_list)
    print(f"\nBuilt {_DIST}/index.html with {len(globals_list)} global + "
          f"{len(kenya_list)} Kenya + {len(business_list)} business + "
          f"{len(tech_list)} tech + {len(sports_list)} sports + "
          f"{len(health_list)} health + {len(culture_list)} culture = {total} stories.")


_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
_CSS = """
    /* Elite Newspaper — refined, fast, accessible */
    :root {
      --paper:#fdfaf3; --paper-2:#f6efe0; --ink:#0a0a0a; --muted:#6b6b6b; --muted2:#9a9a9a;
      --rule:#0a0a0a; --rule-light:#d8cfb8; --rule-faint:#e9e1ca;
      --accent:#b91c1c; --accent-2:#dc2626; --gold:#a16207;
      --shadow: 0 1px 3px rgba(0,0,0,.06), 0 4px 12px rgba(0,0,0,.05);
      --shadow-hover: 0 8px 24px rgba(0,0,0,.10), 0 2px 8px rgba(0,0,0,.06);
      --focus: 0 0 0 3px rgba(185,28,28,.18);
    }
    [data-theme="dark"]{
      --paper:#0c0a08; --paper-2:#14120f; --ink:#f5f3ef; --muted:#a8a29a; --muted2:#7a756e;
      --rule:#f5f3ef; --rule-light:#2a2621; --rule-faint:#1f1c18;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    *::selection{background:var(--accent); color:#fff}
    html{scroll-behavior:smooth; scrollbar-gutter: stable}
    body{
      background: var(--paper);
      color:var(--ink);
      font-family: Georgia, 'Times New Roman', serif;
      line-height:1.6; min-height:100vh;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
      transition: background .2s ease, color .2s ease;
    }
    a:focus-visible, button:focus-visible, input:focus-visible{outline:none; box-shadow: var(--focus); border-radius:4px}
    .skip{position:absolute; left:-9999px; top:auto; width:1px; height:1px; overflow:hidden}
    .skip:focus{left:12px; top:12px; width:auto; height:auto; background:var(--ink); color:#fff; padding:8px 12px; z-index:99; border-radius:999px; font-family:system-ui,sans-serif; font-size:12px; font-weight:700}
    .wrap{max-width:1280px;margin:0 auto;padding:0 18px}
    /* Topbar */
    .topbar{font-family: system-ui, sans-serif; font-size:10.5px; letter-spacing:.06em; color:var(--muted);
      display:flex; gap:12px; align-items:center; justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--rule-faint)}
    .topbar b{color:var(--ink)}
    .weather{display:flex; gap:8px; align-items:center; flex-wrap:wrap}
    .weather i{font-style:normal; background:var(--paper-2); border:1px solid var(--rule-light); padding:3px 9px; border-radius:999px; font-size:10px; font-weight:600}
    .actions{display:flex; gap:8px; align-items:center}
    .btn{appearance:none; border:1px solid var(--rule-light); background:#fff; color:var(--ink); padding:7px 13px; border-radius:999px;
      font-family: system-ui, sans-serif; font-size:11px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; cursor:pointer; transition: all .15s ease}
    .btn:hover{background:var(--paper-2); transform: translateY(-1px)}
    .btn:active{transform: translateY(0)}
    [data-theme="dark"] .btn{background:var(--paper-2); color:var(--ink); border-color:var(--rule-light)}
    /* Masthead */
    header.mast{
      background: var(--paper);
      border-top: 4px solid var(--rule);
      border-bottom: 1px solid var(--rule);
      padding:18px 0 12px; text-align:center; position:relative;
    }
    .mast-top{font-family: system-ui, sans-serif; font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted);
      display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--rule-light); padding-bottom:10px; margin-bottom:14px}
    .mast-title{font-family: 'Times New Roman', Times, Georgia, serif; font-weight:900;
      font-size: clamp(44px, 7.2vw, 78px); letter-spacing:-.03em; line-height:.88; text-transform:uppercase; color:var(--ink); display:flex; align-items:center; justify-content:center; gap:10px}
    .mast-title .kicker{font-size:.26em; letter-spacing:.28em; font-weight:700; color:var(--accent); border:1px solid var(--accent); padding:5px 10px; vertical-align:middle; background: #fff}
    [data-theme="dark"] .mast-title .kicker{background:var(--paper-2)}
    .mast-sub{font-family: system-ui, sans-serif; font-size:10.5px; letter-spacing:.16em; text-transform:uppercase; color:var(--muted);
      border-top:1px solid var(--rule-light); border-bottom:3px double var(--rule); padding:9px 0; margin-top:12px;
      display:flex; gap:14px; justify-content:center; flex-wrap:wrap; align-items:center}
    .mast-sub b{color:var(--ink)}
    .pill{display:inline-flex; align-items:center; gap:6px;
      font-family: system-ui, sans-serif; font-size:10px; letter-spacing:.08em; text-transform:uppercase; font-weight:700;
      color:#065f46; background:#ecfdf5; border:1px solid #6ee7b7; padding:5px 10px; border-radius:999px}
    .pill i{width:6px;height:6px;border-radius:50%;background:#10b981; display:inline-block; animation: pulse 2s infinite}
    @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(16,185,129,.35)}50%{box-shadow:0 0 0 6px rgba(16,185,129,0)}100%{box-shadow:0 0 0 0 rgba(16,185,129,0)}}
    /* Ticker */
    .ticker{background:var(--ink); color:#fff; margin:0 -18px; padding:0; overflow:hidden; display:flex; align-items:center; gap:0; font-family: system-ui, sans-serif; border-top:1px solid var(--rule); border-bottom:1px solid var(--rule)}
    .ticker:hover .ticker-track{animation-play-state: paused}
    .ticker-label{background:var(--accent); color:#fff; padding:9px 14px; font-size:10px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; white-space:nowrap; display:flex; align-items:center; gap:6px}
    .ticker-track{display:flex; gap:28px; animation: marquee 90s linear infinite; white-space:nowrap; padding:9px 0; will-change: transform}
    .ticker-track span{font-size:12.5px; letter-spacing:.01em}
    @keyframes marquee{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
    /* Toolbar */
    .toolbar{display:flex; gap:12px; align-items:center; flex-wrap:wrap; padding:14px 0; border-bottom:1px solid var(--rule-light); background: var(--paper); position:sticky; top:0; z-index:15; backdrop-filter: blur(8px)}
    .tabs{display:flex; gap:0; background: var(--ink); padding:0; border:1px solid var(--rule); flex:1; min-width:280px; border-radius:6px; overflow:hidden}
    .tab{flex:1; appearance:none; border:0; border-right:1px solid #2a2a2a; background: var(--ink); color:#f5f5f5;
      padding:14px 8px; font-family: system-ui, sans-serif; font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
      cursor:pointer; transition: background .15s ease, color .15s ease}
    .tab:last-child{border-right:0}
    .tab:hover{background:#1f1f1f}
    .tab:active{transform: scale(0.98)}
    .tab[aria-selected="true"]{background:#fff; color:var(--ink); box-shadow: inset 0 -3px 0 var(--accent)}
    [data-theme="dark"] .tab[aria-selected="true"]{background:var(--paper-2); color:var(--ink)}
    .tab span{font-weight:400; opacity:.7; margin-left:3px}
    .search{position:relative; flex:0 0 280px; display:flex; align-items:center}
    .search input{width:100%; padding:11px 40px 11px 16px; border:1px solid var(--rule-light); background:#fff; color:var(--ink); border-radius:999px;
      font-family: system-ui, sans-serif; font-size:13.5px; outline:none; transition: border-color .15s ease, box-shadow .15s ease}
    .search input::placeholder{color:var(--muted2); opacity:1}
    .search input:focus{border-color:var(--ink); box-shadow: var(--focus)}
    [data-theme="dark"] .search input{background:var(--paper-2); color:var(--ink); border-color:var(--rule-light)}
    .search button{position:absolute; right:4px; top:4px; bottom:4px; width:36px; border-radius:999px; border:0; background:var(--ink); color:#fff; cursor:pointer; display:grid; place-items:center; transition: background .15s ease}
    .search button:hover{background:var(--accent)}
    /* Layout */
    .layout{display:grid; grid-template-columns: 1fr 360px; gap:22px; margin-top:18px}
    /* Hero */
    .hero{display:grid; grid-template-columns: 1.42fr .95fr; gap:20px;
      background:#fff; border:1px solid var(--rule-light); border-top:3px solid var(--rule);
      padding:18px; text-decoration:none; color:inherit; transition: box-shadow .2s ease}
    [data-theme="dark"] .hero{background:var(--paper-2); border-color:var(--rule-light)}
    .hero:hover{box-shadow: var(--shadow-hover)}
    .hero-media{position:relative; background:#f1f1f1; overflow:hidden; border:1px solid var(--rule-faint)}
    .hero-media img{width:100%; height:100%; min-height:400px; object-fit:cover; display:block; transition: transform .6s ease}
    .hero:hover .hero-media img{transform: scale(1.02)}
    .hero .hbody{padding:4px; display:flex; flex-direction:column; gap:12px}
    .badge{display:inline-flex; align-items:center; gap:6px; font-family: system-ui, sans-serif;
      font-size:10px; letter-spacing:.14em; text-transform:uppercase; font-weight:800;
      color:#fff; background: var(--accent); padding:6px 10px; align-self:flex-start}
    .hero h1{font-family: Georgia, 'Times New Roman', serif; font-size: clamp(28px, 3.1vw, 40px); line-height:1.06; letter-spacing:-.02em; font-weight:900}
    .hero p.sum{color:#222; font-size:15.5px; line-height:1.6; text-align:left; border-left:3px solid var(--rule-faint); padding-left:14px}
    [data-theme="dark"] .hero p.sum{color:#d6d3d1}
    .meta{font-family: system-ui, sans-serif; color:var(--muted); font-size:11px; letter-spacing:.04em; text-transform:uppercase; font-weight:600; display:flex; gap:8px; flex-wrap:wrap; align-items:center; border-top:1px solid var(--rule-faint); padding-top:10px}
    .meta .dot2{width:3px;height:3px;border-radius:50%;background:var(--muted2); display:inline-block}
    .meta .rt{margin-left:auto; background:var(--paper-2); border:1px solid var(--rule-light); padding:2px 7px; border-radius:999px; font-size:10px}
    .readmore{font-family: system-ui, sans-serif; color:var(--ink); text-decoration:none; font-weight:800; font-size:12px; letter-spacing:.06em; text-transform:uppercase; border-bottom:1.5px solid var(--ink); padding-bottom:2px; transition: color .15s ease, border-color .15s ease}
    .readmore:hover{color:var(--accent); border-color:var(--accent)}
    .hero .readmore{margin-top:8px; background:var(--ink); color:#fff; padding:10px 16px; border:0; align-self:flex-start; border-radius:6px}
    .hero .readmore:hover{background:var(--accent)}
    .share{display:flex; gap:6px; margin-top:6px}
    .share a{width:30px; height:30px; border-radius:50%; border:1px solid var(--rule-light); background:#fff; display:grid; place-items:center; font-size:12px; text-decoration:none; color:var(--muted); transition: all .15s ease}
    .share a:hover{background:var(--ink); color:#fff; border-color:var(--ink); transform: translateY(-1px)}
    [data-theme="dark"] .share a{background:var(--paper-2); color:var(--muted); border-color:var(--rule-light)}
    /* Sections */
    .section{margin:26px 0 12px; border-top:3px double var(--rule); padding-top:12px}
    .section-head{display:flex; align-items:baseline; gap:10px; justify-content:space-between; flex-wrap:wrap}
    .section h2{font-family: Georgia, serif; font-size:26px; font-weight:900; letter-spacing:-.02em; text-transform:uppercase; color:var(--ink); display:flex; align-items:center; gap:10px}
    .section h2::before{content:''; width:4px; height:20px; background:var(--accent); display:inline-block; border-radius:2px}
    .section .count{font-family: system-ui, sans-serif; font-size:10px; letter-spacing:.1em; text-transform:uppercase; font-weight:700; color:var(--muted); background:#fff; border:1px solid var(--rule-light); padding:5px 10px; border-radius:999px}
    [data-theme="dark"] .section .count{background:var(--paper-2)}
    /* Grid */
    .grid{display:grid; grid-template-columns: repeat(3, 1fr); gap:18px}
    .card{background:#fff; border:1px solid var(--rule-light); border-top:2.5px solid var(--rule);
      display:flex; flex-direction:column; transition: box-shadow .2s ease, transform .2s ease}
    [data-theme="dark"] .card{background:var(--paper-2); border-color:var(--rule-light)}
    .card:hover{box-shadow: var(--shadow-hover); transform: translateY(-2px)}
    .thumb-wrap{position:relative; aspect-ratio: 16/10.5; overflow:hidden; background:#f5f1e8; display:block; border-bottom:1px solid var(--rule-faint)}
    .thumb{width:100%; height:100%; object-fit:cover; display:block; background:#f5f1e8; transition: transform .5s ease}
    .card:hover .thumb{transform: scale(1.04)}
    .thumb--ph{display:grid; place-items:center; color:#a8a29a; font-size:28px; background: repeating-linear-gradient(45deg, #f5f1e8, #f5f1e8 10px, #ede6d5 10px, #ede6d5 20px)}
    .srcpill{font-family: system-ui, sans-serif; font-size:9px; letter-spacing:.12em; text-transform:uppercase; font-weight:800;
      color:var(--accent); background:#fff; border:1px solid var(--rule-light); padding:4px 8px; box-shadow:0 1px 3px rgba(0,0,0,.06)}
    .srcpill--over{position:absolute; top:10px; left:10px; z-index:2}
    .save{position:absolute; top:10px; right:10px; z-index:2; width:32px; height:32px; border-radius:50%; border:1px solid var(--rule-light);
      background:rgba(255,255,255,.92); display:grid; place-items:center; cursor:pointer; font-size:14px; transition: all .15s ease}
    .save:hover{transform: scale(1.05)}
    .save[aria-pressed="true"]{background:var(--ink); color:#fff; border-color:var(--ink)}
    .cbody{padding:14px 16px 14px; display:flex; flex-direction:column; gap:10px; flex:1}
    .cbody h3{line-height:1.2; font-family: Georgia, serif; font-weight:900; letter-spacing:-.015em}
    .cbody h3 a{color:var(--ink); text-decoration:none; font-size:17px; display:block; font-family: Georgia, serif}
    .cbody h3 a:hover{text-decoration:underline; text-decoration-thickness:1.5px; text-underline-offset:3px}
    .card .sum{color:#2b2b2b; font-size:13.4px; line-height:1.6; text-align:left; display:block}
    [data-theme="dark"] .card .sum{color:#cbd5e1}
    .card .meta{margin-top:auto; padding-top:10px; border-top:1px solid var(--rule-faint); display:flex; align-items:center; justify-content:space-between; font-size:10px; gap:8px}
    .card .share{margin-top:4px}
    .card .share a{width:26px; height:26px; font-size:11px}
    /* Sidebar */
    .sidebar{display:flex; flex-direction:column; gap:18px; position:sticky; top:78px; align-self:start}
    .box{background:#fff; border:1px solid var(--rule-light); border-top:3px solid var(--rule); padding:16px; box-shadow: var(--shadow)}
    [data-theme="dark"] .box{background:var(--paper-2)}
    .box h3{font-family: system-ui, sans-serif; font-size:11px; letter-spacing:.14em; text-transform:uppercase; font-weight:800; border-bottom:1px solid var(--rule-light); padding-bottom:10px; margin-bottom:12px; display:flex; align-items:center; justify-content:space-between}
    .box h3 span{font-weight:400; text-transform:none; letter-spacing:0; font-size:11px; color:var(--muted)}
    .trend{display:flex; gap:12px; padding:10px 0; border-bottom:1px solid var(--rule-faint); transition: background .15s ease}
    .trend:hover{background:var(--paper-2); margin:0 -16px; padding:10px 16px}
    .trend:last-child{border-bottom:0}
    .trend i{font-style:normal; font-family: Georgia, serif; font-size:24px; font-weight:900; color:var(--rule-light); line-height:1; min-width:28px}
    .trend a{font-size:13.5px; font-weight:700; line-height:1.35; color:var(--ink); text-decoration:none; display:block}
    .trend a:hover{color:var(--accent)}
    .trend span{font-family: system-ui, sans-serif; font-size:10px; color:var(--muted); display:block; margin-top:4px}
    .newsletter input{width:100%; padding:11px 12px; border:1px solid var(--rule-light); border-radius:6px; font-size:13px; margin-bottom:10px; background:#fff; color:var(--ink)}
    #newsletter{scroll-margin-top:90px}
    .search input{color:var(--ink) !important; background:#fff !important}
    [data-theme="dark"] .search input{color:var(--ink) !important; background:var(--paper-2) !important}
    .newsletter input::placeholder{color:var(--muted2)}
    [data-theme="dark"] .newsletter input{background:var(--paper-2); color:var(--ink)}
    .newsletter button{width:100%; padding:11px; background:var(--ink); color:#fff; border:0; border-radius:6px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; cursor:pointer; transition: background .15s ease, transform .08s ease; touch-action: manipulation; user-select:none; -webkit-tap-highlight-color: transparent; min-height:44px}
    .newsletter button:hover{background:var(--accent)}
    .backtop{position:fixed; bottom:18px; right:18px; width:44px; height:44px; border-radius:50%; background:var(--ink); color:#fff; border:0; display:grid; place-items:center; cursor:pointer; box-shadow: var(--shadow-hover); opacity:0; pointer-events:none; transition: opacity .2s ease, transform .2s ease; z-index:20}
    .backtop.show{opacity:1; pointer-events:auto}
    .backtop:hover{transform: translateY(-2px)}
    footer{margin:36px 0 28px; padding:18px 0; border-top:3px double var(--rule); border-bottom:1px solid var(--rule);
      color:var(--muted); font-family: system-ui, sans-serif; font-size:11px; line-height:1.6; display:flex; flex-wrap:wrap; gap:10px 18px; justify-content:center; text-align:center}
    footer b{color:var(--ink)}
    @media (max-width: 1100px){ .grid{grid-template-columns: repeat(2, 1fr)} .layout{grid-template-columns: 1fr} .sidebar{position:static} }
    @media (max-width: 900px){
      .hero{grid-template-columns:1fr}
      .hero-media img{min-height:280px}
      .topbar{flex-direction:column; gap:8px; text-align:center}
      .toolbar{flex-direction:column; align-items:stretch}
      .search{flex:1 1 auto}
    }
    @media (max-width: 620px){
      .grid{grid-template-columns:1fr}
      .wrap{padding:0 12px}
      .mast-title{font-size:42px}
    }
    @media print {
      body{background:#fff !important; -webkit-print-color-adjust:exact; print-color-adjust:exact}
      .topbar, .ticker, .toolbar, .actions, .search, .save, #themeToggle, .share, .backtop{display:none !important}
      .layout{display:block}
      .sidebar{display:none !important}
      .hero, .card{break-inside:avoid; box-shadow:none !important; border:1px solid #ccc !important}
      .thumb-wrap, .hero-media{ -webkit-print-color-adjust:exact; print-color-adjust:exact}
      .thumb, .hero-media img{display:block !important; visibility:visible !important; opacity:1 !important}
      a{color:var(--ink) !important; text-decoration:none !important}
      footer{break-before:avoid}
    }
"""





def _card(it, idx):
    uid = re.sub(r"[^a-z0-9]+", "-", it["title"].lower())[:40] + "-" + str(idx)
    rt = _reading_time(it["summary"])
    share = _share_url(it["link"], it["title"])
    if it["img"]:
        media = f'<div class="thumb-wrap"><img loading="lazy" decoding="async" referrerpolicy="no-referrer" class="thumb" src="{html.escape(it["img"])}" alt="{html.escape(it["title"])}" onerror="this.style.display=\'none\'"><span class="srcpill srcpill--over">{html.escape(it["source"])}</span><button class="save" aria-pressed="false" aria-label="Save" title="Save">☆</button></div>'
    else:
        media = f'<div class="thumb-wrap"><div class="thumb thumb--ph">📰</div><span class="srcpill srcpill--over">{html.escape(it["source"])}</span><button class="save" aria-pressed="false" aria-label="Save">☆</button></div>'
    return f"""<article class="card" data-id="{html.escape(uid)}" data-link="{html.escape(it["link"], quote=True)}" data-title="{html.escape(it["title"])}" data-source="{html.escape(it["source"])}">
      {media}
      <div class="cbody">
        <h3><a href="{html.escape(it["link"], quote=True)}" target="_blank" rel="noopener">{html.escape(it["title"])}</a></h3>
        <p class="sum">{html.escape(_shorten(it["summary"]))}</p>
        <p class="meta"><span>{_timeago(it["pub_int"])}</span><span class="dot2"></span><span class="rt">{rt} min</span><a class="readmore" href="{html.escape(it["link"], quote=True)}" target="_blank" rel="noopener">Read more →</a></p>
        <div class="share"><a href="{html.escape(share["x"], quote=True)}" target="_blank" rel="noopener" aria-label="Share on X">𝕏</a><a href="{html.escape(share["wa"], quote=True)}" target="_blank" rel="noopener" aria-label="Share on WhatsApp">WA</a><a href="#" onclick="navigator.clipboard.writeText('{html.escape(share["copy"], quote=True)}');this.textContent='✓';setTimeout(()=>this.textContent='⧉',1200);return false" aria-label="Copy link">⧉</a></div>
      </div>
    </article>"""


def render(globals_list, kenya_list, business_list=None, tech_list=None, sports_list=None, health_list=None, culture_list=None, debug=False):
    business_list = business_list or []
    tech_list = tech_list or []
    sports_list = sports_list or []
    health_list = health_list or []
    culture_list = culture_list or []
    today = dt.date.today()
    today_label = (f"{today.strftime('%A')}, {today.day} "
                   f"{today.strftime('%B')} {today.year}")
    e = html.escape
    hero = globals_list[0] if globals_list else None
    cards_g = "".join(_card(it, i) for i, it in enumerate(globals_list[1:]))
    cards_k = "".join(_card(it, i) for i, it in enumerate(kenya_list))
    cards_b = "".join(_card(it, i) for i, it in enumerate(business_list))
    cards_t = "".join(_card(it, i) for i, it in enumerate(tech_list))
    cards_s = "".join(_card(it, i) for i, it in enumerate(sports_list))
    cards_h = "".join(_card(it, i) for i, it in enumerate(health_list))
    cards_c = "".join(_card(it, i) for i, it in enumerate(culture_list))
    hero_html = ""
    if hero:
        if hero["img"]:
            hero_media = f'<div class="hero-media"><img src="{e(hero["img"])}" alt="{e(hero["title"])}" loading="eager" decoding="async" referrerpolicy="no-referrer" onerror="this.style.display=\'none\'"></div>'
        else:
            hero_media = '<div class="hero-media"><div class="thumb thumb--ph" style="height:100%;min-height:360px">📰</div></div>'
        hero_html = f"""<a href="{e(hero['link'], True)}" target="_blank" rel="noopener" class="hero">
        {hero_media}
        <div class="hbody">
          <span class="badge">★ Top story · {e(hero['source'])}</span>
          <h1>{e(hero['title'])}</h1>
          <p class="sum">{e(_shorten(hero['summary'], 260))}</p>
          <p class="meta"><span>{e(hero['source'])}</span><span class="dot2"></span><span>{_timeago(hero['pub_int'])}</span></p>
          <span class="readmore">Read the full story →</span>
        </div>
      </a>"""
    updated = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).strftime("%H:%M EAT")
    total = len(globals_list) + len(kenya_list) + len(business_list) + len(tech_list) + len(sports_list) + len(health_list) + len(culture_list)
    ticker_items = (globals_list[:5] + kenya_list[:3] + business_list[:2])[:8]
    ticker_html = " • ".join(html.escape(x["title"][:70]) for x in ticker_items)
    trending = (globals_list[1:4] + kenya_list[:3] + business_list[:2] + tech_list[:1])
    trending_html = "".join(f"<div class=\"trend\"><i>{i+1:02d}</i><div><a href=\"{html.escape(x['link'], quote=True)}\" target=\"_blank\" rel=\"noopener\">{html.escape(x['title'])}</a><span>{html.escape(x['source'])} · {_timeago(x['pub_int'])}</span></div></div>" for i,x in enumerate(trending[:7]))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Global Digest — The International & Kenya Morning Paper</title>
<meta name="description" content="The {total} biggest stories today — World {len(globals_list)}, Kenya {len(kenya_list)}, Business {len(business_list)}, Tech {len(tech_list)}, Sports {len(sports_list)} — newspaper edition.">
<meta property="og:title" content="Global Digest — Today's Front Page">
<meta property="og:type" content="website">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📰</text></svg>">
<style>{_CSS}</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <span class="weather"><i>☁︎ 22° Nairobi</i> <i>USD/KES 129.5</i> <b>{today_label}</b></span>
      <span class="actions">
        <button class="btn btn--dark" id="themeToggle" aria-label="Toggle theme">◐ Theme</button>
        <a class="btn" href="#newsletter">Subscribe</a>
      </span>
    </div>
    <header class="mast">
      <div class="mast-top"><span>VOL. I — NO. 1</span><span>FOUNDED 2026 • NAIROBI • LONDON • NEW YORK</span><span>Price: Free · Edition {updated}</span></div>
      <div class="mast-title"><span class="kicker">INTERNATIONAL</span> The Global Digest <span style="font-size:10px; letter-spacing:.12em; background:var(--accent); color:#fff; padding:4px 8px; vertical-align:middle; margin-left:8px">ELITE EDITION</span></div>
      <div class="mast-sub"><span><b>EST. 2026</b> — WORLD · KENYA · BUSINESS · TECH · SPORTS · HEALTH · CULTURE · {total} STORIES DAILY</span><span class="pill"><i></i> Live · Updated {updated}</span></div>
    </header>

    <div class="ticker" aria-label="Breaking">
      <div class="ticker-label">Breaking</div>
      <div class="ticker-track"><span>{ticker_html} • {ticker_html}</span></div>
    </div>

    <div class="toolbar">
      <nav class="tabs" role="tablist" aria-label="News sections">
        <button class="tab" role="tab" aria-selected="true" data-filter="all">All <span>{total}</span></button>
        <button class="tab" role="tab" aria-selected="false" data-filter="world">World <span>{len(globals_list)}</span></button>
        <button class="tab" role="tab" aria-selected="false" data-filter="kenya">Kenya <span>{len(kenya_list)}</span></button>
        <button class="tab" role="tab" aria-selected="false" data-filter="business">Business <span>{len(business_list)}</span></button>
        <button class="tab" role="tab" aria-selected="false" data-filter="tech">Tech <span>{len(tech_list)}</span></button>
        <button class="tab" role="tab" aria-selected="false" data-filter="sports">Sports <span>{len(sports_list)}</span></button>
        <button class="tab" role="tab" aria-selected="false" data-filter="health">Health <span>{len(health_list)}</span></button>
        <button class="tab" role="tab" aria-selected="false" data-filter="culture">Culture <span>{len(culture_list)}</span></button>
      </nav>
      <div class="search"><input id="search" type="search" placeholder="Search headlines…" aria-label="Search" autocomplete="off"><button type="button" aria-label="Search">⌕</button></div>
    </div>

    <div class="layout">
      <main>
        <div id="sec-hero" data-section="hero">{hero_html}</div>

        <div id="sec-world" data-section="world">
          <div class="section"><div class="section-head"><h2>World</h2><span class="count">{len(globals_list)} stories — BBC · Guardian · NYT · DW</span></div></div>
          <section class="grid">{cards_g}</section>
        </div>

        <div id="sec-kenya" data-section="kenya">
          <div class="section"><div class="section-head"><h2>Kenya</h2><span class="count">{len(kenya_list)} stories — KBC · Nairobi Wire · Kenyans</span></div></div>
          <section class="grid">{cards_k}</section>
        </div>

        <div id="sec-business" data-section="business">
          <div class="section"><div class="section-head"><h2>Business</h2><span class="count">{len(business_list)} stories</span></div></div>
          <section class="grid">{cards_b}</section>
        </div>

        <div id="sec-tech" data-section="technology">
          <div class="section"><div class="section-head"><h2>Technology</h2><span class="count">{len(tech_list)} stories</span></div></div>
          <section class="grid">{cards_t}</section>
        </div>

        <div id="sec-sports" data-section="sports">
          <div class="section"><div class="section-head"><h2>Sports</h2><span class="count">{len(sports_list)} stories</span></div></div>
          <section class="grid">{cards_s}</section>
        </div>

        <div id="sec-health" data-section="health">
          <div class="section"><div class="section-head"><h2>Health</h2><span class="count">{len(health_list)} stories</span></div></div>
          <section class="grid">{cards_h}</section>
        </div>

        <div id="sec-culture" data-section="culture">
          <div class="section"><div class="section-head"><h2>Culture</h2><span class="count">{len(culture_list)} stories</span></div></div>
          <section class="grid">{cards_c}</section>
        </div>
      </main>

      <aside class="sidebar">
        <div class="box">
          <h3>Trending Now</h3>
          {trending_html}
        </div>
        <div class="box" id="savedBox" style="display:none">
          <h3>★ Saved Articles</h3>
          <div id="savedList"></div>
        </div>
        <div class="box newsletter" id="newsletter">
          <h3>☕ Morning Briefing</h3>
          <p style="font-size:13px; color:var(--muted); margin-bottom:10px">Get the front page in your inbox at 6am EAT.</p>
          <input type="email" placeholder="Your email" aria-label="Email">
          <button onclick="this.textContent='✓ Subscribed';this.style.background='var(--accent)'">Subscribe — Free</button>
          <p style="font-size:11px; color:var(--muted2); margin-top:8px">No spam. Unsubscribe anytime. Built daily.</p>
        </div>
        <div class="box">
          <h3>Today's Edition</h3>
          <p style="font-size:13px; line-height:1.6; color:var(--muted)"><b style="color:var(--ink)">{today_label}</b> — {total} stories hand-picked. Updated {updated} EAT. <a href="#" onclick="window.print();return false" style="color:var(--accent); font-weight:700">Print edition →</a></p>
        </div>
      </aside>
    </div>

    <footer>
      <span><b>Global Digest</b> — newspaper front page, rebuilt daily at {updated} ({today_label}).</span>
      <span>Sources: BBC, Guardian, NYT, DW, Al Jazeera, KBC, Kenyans, Nairobi Wire, Capital FM.</span>
      <span>Auto-updates daily 06:00 EAT via GitHub Actions. “Read more” opens publisher. No tracking.</span>
    </footer>
  </div>
<script>
(function(){{
  const tabs=document.querySelectorAll('.tab');
  const secs={{hero:document.getElementById('sec-hero'),world:document.getElementById('sec-world'),kenya:document.getElementById('sec-kenya'),business:document.getElementById('sec-business'),tech:document.getElementById('sec-tech'),sports:document.getElementById('sec-sports'),health:document.getElementById('sec-health'),culture:document.getElementById('sec-culture')}};
  function setFilter(f){{
    tabs.forEach(t=>t.setAttribute('aria-selected', String(t.dataset.filter===f)));
    Object.keys(secs).forEach(k=>{{ if(secs[k]) secs[k].style.display=''; }});
    if(f==='all'){{}}
    else if(f==='world'){{ if(secs.kenya) secs.kenya.style.display='none'; if(secs.business) secs.business.style.display='none'; if(secs.tech) secs.tech.style.display='none'; if(secs.sports) secs.sports.style.display='none'; if(secs.health) secs.health.style.display='none'; if(secs.culture) secs.culture.style.display='none'; }}
    else if(f==='kenya'){{ if(secs.hero) secs.hero.style.display='none'; if(secs.world) secs.world.style.display='none'; if(secs.business) secs.business.style.display='none'; if(secs.tech) secs.tech.style.display='none'; if(secs.sports) secs.sports.style.display='none'; if(secs.health) secs.health.style.display='none'; if(secs.culture) secs.culture.style.display='none'; }}
    else if(f==='business'){{ if(secs.hero) secs.hero.style.display='none'; if(secs.world) secs.world.style.display='none'; if(secs.kenya) secs.kenya.style.display='none'; if(secs.tech) secs.tech.style.display='none'; if(secs.sports) secs.sports.style.display='none'; if(secs.health) secs.health.style.display='none'; if(secs.culture) secs.culture.style.display='none'; }}
    else if(f==='tech'){{ if(secs.hero) secs.hero.style.display='none'; if(secs.world) secs.world.style.display='none'; if(secs.kenya) secs.kenya.style.display='none'; if(secs.business) secs.business.style.display='none'; if(secs.sports) secs.sports.style.display='none'; if(secs.health) secs.health.style.display='none'; if(secs.culture) secs.culture.style.display='none'; }}
    else if(f==='sports'){{ if(secs.hero) secs.hero.style.display='none'; if(secs.world) secs.world.style.display='none'; if(secs.kenya) secs.kenya.style.display='none'; if(secs.business) secs.business.style.display='none'; if(secs.tech) secs.tech.style.display='none'; if(secs.health) secs.health.style.display='none'; if(secs.culture) secs.culture.style.display='none'; }}
    else if(f==='health'){{ if(secs.hero) secs.hero.style.display='none'; if(secs.world) secs.world.style.display='none'; if(secs.kenya) secs.kenya.style.display='none'; if(secs.business) secs.business.style.display='none'; if(secs.tech) secs.tech.style.display='none'; if(secs.sports) secs.sports.style.display='none'; if(secs.culture) secs.culture.style.display='none'; }}
    else if(f==='culture'){{ if(secs.hero) secs.hero.style.display='none'; if(secs.world) secs.world.style.display='none'; if(secs.kenya) secs.kenya.style.display='none'; if(secs.business) secs.business.style.display='none'; if(secs.tech) secs.tech.style.display='none'; if(secs.sports) secs.sports.style.display='none'; if(secs.health) secs.health.style.display='none'; }}
    window.scrollTo({{top:0, behavior:'smooth'}});
  }}
  tabs.forEach(t=>{{
    t.addEventListener('click', ()=>setFilter(t.dataset.filter));
    t.addEventListener('touchstart', e=>{{ e.preventDefault(); setFilter(t.dataset.filter); }}, {{passive:false}});
  }});
  document.querySelectorAll('.section h2').forEach(h=>{{
    h.style.cursor='pointer';
    h.title='Tap to filter';
    h.addEventListener('click', ()=>{{
      const txt=h.textContent.toLowerCase().trim();
      const map={{world:'world', kenya:'kenya', business:'business', technology:'tech', tech:'tech', sports:'sports', health:'health', culture:'culture'}};
      for(const k in map){{ if(txt.includes(k)) {{ setFilter(map[k]); document.querySelector('.toolbar')?.scrollIntoView({{behavior:'smooth'}}); break; }} }}
    }});
  }});
  const q=document.getElementById('search');
  const searchBtn=document.querySelector('.search button');
  function doSearch(){{
    const v=(q? q.value.toLowerCase().trim() : '');
    let visible=0;
    document.querySelectorAll('.card').forEach(c=>{{
      const txt=(c.dataset.title + ' ' + c.dataset.source + ' ' + c.textContent).toLowerCase();
      const show = !v || txt.includes(v);
      c.style.display= show ? '' : 'none';
      if(show) visible++;
    }});
    const hero=document.getElementById('sec-hero');
    let heroVisible=false;
    if(hero){{
      const ht=hero.textContent.toLowerCase();
      heroVisible = !v || ht.includes(v);
      if(v && !heroVisible && visible===0) hero.style.display='none'; else hero.style.display='';
      if(heroVisible) visible++;
    }}
    document.querySelectorAll('[data-section]').forEach(sec=>{{
      if(sec.id==='sec-hero') return;
      const heading= sec.querySelector('h2') ? sec.querySelector('h2').textContent.toLowerCase() : '';
      const cards=sec.querySelectorAll('.card');
      const any=[...cards].some(c=>c.style.display!=='none');
      const headingMatch = v && heading.includes(v);
      sec.style.display = (!v || any || headingMatch) ? '' : 'none';
    }});
    let msg=document.getElementById('noResults');
    if(v && visible===0){{
      if(!msg){{ msg=document.createElement('div'); msg.id='noResults'; msg.style.cssText='padding:24px; text-align:center; color:var(--muted); font-family:system-ui,sans-serif; border:1px dashed var(--rule-light); margin:16px 0; background:#fff'; msg.innerHTML='No results for "<b></b>" — try "Ruto", "Trump" or "Business" or <a href="#" onclick="document.getElementById(\'search\').value=\'\';document.getElementById(\'search\').dispatchEvent(new Event(\'input\'));return false" style="color:var(--accent)">clear</a>'; document.querySelector('main').prepend(msg); }}
      msg.querySelector('b').textContent=v;
      msg.style.display='';
    }} else if(msg) msg.style.display='none';
  }}
  if(q){{ q.addEventListener('input', doSearch); q.addEventListener('search', doSearch); q.addEventListener('keydown', e=>{{ if(e.key==='Enter'){{ e.preventDefault(); doSearch(); }} }}); }}
  if(searchBtn) searchBtn.addEventListener('click', e=>{{ e.preventDefault(); doSearch(); q.focus(); }});
  let btn=null;
  try{{
    btn=document.getElementById('themeToggle');
    const saved=localStorage.getItem('gd-theme');
    if(saved) document.documentElement.setAttribute('data-theme', saved);
    if(btn) btn.addEventListener('click', ()=>{{
      try{{
        const cur=document.documentElement.getAttribute('data-theme')==='dark' ? 'light' : 'dark';
        if(cur==='light') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme','dark');
        localStorage.setItem('gd-theme', cur==='light'?'light':'dark');
      }}catch(e){{}}
    }});
  }}catch(e){{}}
  try{{
    const subLink=document.querySelector('a[href="#newsletter"]');
    if(subLink) subLink.addEventListener('click', e=>{{ e.preventDefault(); const el=document.getElementById('newsletter'); if(el) el.scrollIntoView({{behavior:'smooth', block:'start'}}); }});
  }}catch(e){{}}
  let savedIds=[];
  try{{ savedIds=JSON.parse(localStorage.getItem('gd-saved')||'[]'); }}catch(e){{ savedIds=[]; }}
  const savedBox=document.getElementById('savedBox'), savedList=document.getElementById('savedList');
  function renderSaved(){{
    if(!savedIds.length){{ if(savedBox) savedBox.style.display='none'; return; }}
    if(savedBox) savedBox.style.display=''; if(savedList) savedList.innerHTML=savedIds.map(function(id){{ var el=document.querySelector('[data-id="'+id+'"]'); return el ? '<div style="padding:8px 0; border-bottom:1px solid var(--rule-faint)"><a href="'+el.dataset.link+'" target="_blank" style="font-weight:700; color:var(--ink); text-decoration:none">'+el.dataset.title+'</a><div style="font-size:11px; color:var(--muted)">'+el.dataset.source+'</div></div>' : ''; }}).join('');
  }}
  document.querySelectorAll('.card').forEach(function(c){{ c.addEventListener('click', function(e){{ if(e.target.closest('.save')){{ var id=c.dataset.id; var i=savedIds.indexOf(id); var b=c.querySelector('.save'); if(i>-1){{ savedIds.splice(i,1); b.setAttribute('aria-pressed','false'); b.textContent='☆'; }} else {{ savedIds.push(id); b.setAttribute('aria-pressed','true'); b.textContent='★'; }} localStorage.setItem('gd-saved', JSON.stringify(savedIds)); renderSaved(); }} }})}});
  renderSaved();
}})();
</script>
</body>
</html>
"""



if __name__ == "__main__":
    build()