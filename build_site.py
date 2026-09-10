#!/usr/bin/env python3
"""Global Digest - daily top-stories site builder (Python stdlib only).

Fetches free RSS feeds (no API keys), dedupes, ranks, and renders a
self-contained newspaper-style index.html with ~15 world + 10 Kenya + 6 business
+ 6 tech + 6 sports stories (≈43 daily). Meant to run daily from GitHub Actions; the
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

GLOBAL_COUNT = int(os.environ.get("DIGEST_GLOBAL", "15"))
KENYA_COUNT = int(os.environ.get("DIGEST_KENYA", "10"))
BUSINESS_COUNT = int(os.environ.get("DIGEST_BUSINESS", "6"))
TECH_COUNT = int(os.environ.get("DIGEST_TECH", "6"))
SPORTS_COUNT = int(os.environ.get("DIGEST_SPORTS", "6"))
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
    """Upgrade tiny RSS thumbs to readable size (BBC 80/240 -> 800)."""
    if not u or _is_placeholder_img(u):
        return ""
    if "ichef.bbci.co.uk" in u:
        # /80/ /240/ /240x135/ -> 800
        u = re.sub(r"/\d+/", "/800/", u)
        u = re.sub(r"/\d+x\d+/", "/800/", u)
        u = u.replace("/80/", "/800/").replace("/240/", "/800/")
    # Guardian i.guim.co.uk -> ensure width 800+
    if "i.guim.co.uk" in u and "width=" in u:
        u = re.sub(r"width=\d+", "width=800", u)
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
        global GLOBAL_COUNT, KENYA_COUNT, BUSINESS_COUNT, TECH_COUNT, SPORTS_COUNT
        GLOBAL_COUNT, KENYA_COUNT, BUSINESS_COUNT, TECH_COUNT, SPORTS_COUNT = 5, 3, 2, 2, 2

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
    g = sorted([x for x in ranked if x["kind"] == "global"], key=lambda x: -x["score"])
    k = sorted([x for x in ranked if x["kind"] == "kenya"], key=lambda x: -x["score"])
    b = sorted([x for x in ranked if x["kind"] == "business"], key=lambda x: -x["score"])
    t = sorted([x for x in ranked if x["kind"] == "technology"], key=lambda x: -x["score"])
    s = sorted([x for x in ranked if x["kind"] == "sports"], key=lambda x: -x["score"])
    globals_list = g[:GLOBAL_COUNT]
    kenya_list = k[:KENYA_COUNT]
    business_list = b[:BUSINESS_COUNT]
    tech_list = t[:TECH_COUNT]
    sports_list = s[:SPORTS_COUNT]

    # Fill in missing / Google placeholder images (concurrently, best-effort).
    all_selected = globals_list + kenya_list + business_list + tech_list + sports_list
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

    html_out = render(globals_list, kenya_list, business_list, tech_list, sports_list, debug)
    os.makedirs(os.path.join(_DIST, "data"), exist_ok=True)
    with open(os.path.join(_DIST, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    with open(os.path.join(_DIST, "data", "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "global": globals_list, "kenya": kenya_list,
                   "business": business_list, "technology": tech_list, "sports": sports_list}, f,
                  ensure_ascii=False, indent=2)
    total = len(globals_list)+len(kenya_list)+len(business_list)+len(tech_list)+len(sports_list)
    print(f"\nBuilt {_DIST}/index.html with {len(globals_list)} global + "
          f"{len(kenya_list)} Kenya + {len(business_list)} business + "
          f"{len(tech_list)} tech + {len(sports_list)} sports = {total} stories.")


_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
_CSS = """
    :root {
      --paper:#fdfaf3; --paper-edge:#f3ece0; --ink:#0a0a0a; --muted:#6b6b6b; --muted2:#9a9a9a;
      --rule:#0a0a0a; --rule-light:#d8cfb8; --rule-faint:#e9e1ca;
      --accent:#b91c1c; --shadow: 0 1px 4px rgba(0,0,0,.08);
    }
    *{box-sizing:border-box;margin:0;padding:0}
    html{scroll-behavior:smooth}
    body{
      background: var(--paper) url("data:image/svg+xml,%3Csvg width='100' height='100' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M0 0h100v100H0z' fill='none'/%3E%3Cpath d='M0 0l100 100M100 0L0 100' stroke='%23e9e1ca' stroke-opacity='.18' stroke-width='.5'/%3E%3C/svg%3E");
      color:var(--ink);
      font-family: Georgia, 'Times New Roman', serif;
      line-height:1.55; min-height:100vh;
      -webkit-font-smoothing: antialiased;
    }
    .wrap{max-width:1260px;margin:0 auto;padding:0 18px}
    /* Masthead - newspaper */
    header.mast{
      background: var(--paper);
      border-top: 4px solid var(--rule);
      border-bottom: 1px solid var(--rule);
      padding:14px 0 10px; text-align:center; position:relative;
    }
    .mast-top{font-family: system-ui, sans-serif; font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted);
      display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--rule-light); padding-bottom:8px; margin-bottom:12px}
    .mast-top span{white-space:nowrap}
    .mast-title{font-family: 'Times New Roman', Times, Georgia, serif; font-weight:900;
      font-size: clamp(42px, 7vw, 72px); letter-spacing:-.02em; line-height:.9; text-transform:uppercase; color:var(--ink)}
    .mast-title .thin{font-weight:400; letter-spacing:.08em; font-size:.55em; vertical-align:middle; margin:0 6px}
    .mast-sub{font-family: system-ui, sans-serif; font-size:10.5px; letter-spacing:.16em; text-transform:uppercase; color:var(--muted);
      border-top:1px solid var(--rule-light); border-bottom:3px double var(--rule); padding:7px 0; margin-top:10px;
      display:flex; gap:14px; justify-content:center; flex-wrap:wrap}
    .mast-sub b{color:var(--ink)}
    .pill{position:absolute; top:16px; right:0; display:inline-flex; align-items:center; gap:6px;
      font-family: system-ui, sans-serif; font-size:10px; letter-spacing:.08em; text-transform:uppercase; font-weight:700;
      color:#065f46; background:#ecfdf5; border:1px solid #6ee7b7; padding:5px 10px; border-radius:999px}
    .pill i{width:6px;height:6px;border-radius:50%;background:#10b981; display:inline-block}
    .datebar{display:none}
    /* Tabs - newspaper section nav */
    .tabs{display:flex; gap:0; margin:0; background: var(--ink); padding:0; border-top:1px solid var(--rule); border-bottom:1px solid var(--rule)}
    .tab{flex:1; appearance:none; border:0; border-right:1px solid #2a2a2a; background: var(--ink); color:#f5f5f5;
      padding:12px 10px; font-family: system-ui, sans-serif; font-size:12px; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
      cursor:pointer; transition: background .15s ease}
    .tab:last-child{border-right:0}
    .tab:hover{background:#1f1f1f}
    .tab[aria-selected="true"]{background:#fff; color:var(--ink); box-shadow: inset 0 -3px 0 var(--accent)}
    .tab span{font-weight:400; opacity:.7; margin-left:4px}
    /* Sections */
    .section{margin:22px 0 10px; border-top:3px double var(--rule); padding-top:10px}
    .section-head{display:flex; align-items:baseline; gap:10px; justify-content:space-between}
    .section h2{font-family: Georgia, serif; font-size:26px; font-weight:900; letter-spacing:-.02em; text-transform:uppercase; color:var(--ink)}
    .section .count{font-family: system-ui, sans-serif; font-size:10px; letter-spacing:.1em; text-transform:uppercase; font-weight:700; color:var(--muted)}
    .rule{display:none}
    /* Hero - newspaper lead */
    .hero{display:grid; grid-template-columns: 1.45fr .9fr; gap:18px;
      background:#fff; border:1px solid var(--rule-light); border-top:2px solid var(--rule);
      padding:16px; text-decoration:none; color:inherit; margin-top:16px}
    .hero-media{position:relative; background:#eee; overflow:hidden; border:1px solid var(--rule-faint)}
    .hero-media img{width:100%; height:100%; min-height:360px; object-fit:cover; display:block}
    .hero .hbody{padding:2px 4px; display:flex; flex-direction:column; gap:10px}
    .badge{display:inline-flex; align-items:center; gap:6px; font-family: system-ui, sans-serif;
      font-size:10px; letter-spacing:.14em; text-transform:uppercase; font-weight:800;
      color:var(--accent); border:1px solid var(--accent); padding:4px 8px; align-self:flex-start}
    .hero h1{font-family: Georgia, 'Times New Roman', serif; font-size: clamp(26px, 3vw, 38px); line-height:1.07; letter-spacing:-.02em; font-weight:900}
    .hero p.sum{color:#222; font-size:15px; line-height:1.55; text-align:justify; hyphens:auto; border-left:3px solid var(--rule-faint); padding-left:12px}
    .meta{font-family: system-ui, sans-serif; color:var(--muted); font-size:11px; letter-spacing:.04em; text-transform:uppercase; font-weight:600; display:flex; gap:8px; flex-wrap:wrap; border-top:1px solid var(--rule-faint); padding-top:8px}
    .meta .dot2{width:2px;height:2px;border-radius:50%;background:var(--muted2); display:inline-block; align-self:center}
    .readmore{font-family: system-ui, sans-serif; color:var(--ink); text-decoration:none; font-weight:800; font-size:12px; letter-spacing:.06em; text-transform:uppercase; border-bottom:1px solid var(--ink); padding-bottom:1px}
    .readmore:hover{color:var(--accent); border-color:var(--accent)}
    .hero .readmore{margin-top:6px; background:var(--ink); color:#fff; padding:8px 14px; border-radius:0; border:0; align-self:flex-start}
    .hero .readmore:hover{background:var(--accent)}
    /* Grid - newspaper columns */
    .grid{display:grid; grid-template-columns: repeat(3, 1fr); gap:18px}
    .card{background:#fff; border:1px solid var(--rule-light); border-top:2px solid var(--rule);
      display:flex; flex-direction:column; transition: box-shadow .18s ease}
    .card:hover{box-shadow: var(--shadow)}
    .thumb-wrap{position:relative; aspect-ratio: 16/10; overflow:hidden; background:#f5f1e8; display:block; border-bottom:1px solid var(--rule-faint)}
    .thumb{width:100%; height:100%; object-fit:cover; display:block; background:#f5f1e8}
    .thumb--ph{display:grid; place-items:center; color:#a8a29a; font-size:28px; background: repeating-linear-gradient(45deg, #f5f1e8, #f5f1e8 10px, #ede6d5 10px, #ede6d5 20px)}
    .srcpill{font-family: system-ui, sans-serif; font-size:9px; letter-spacing:.12em; text-transform:uppercase; font-weight:800;
      color:var(--accent); background:#fff; border:1px solid var(--rule-light); padding:3px 7px}
    .srcpill--over{position:absolute; top:8px; left:8px; z-index:2; box-shadow:0 1px 4px rgba(0,0,0,.12)}
    .cbody{padding:12px 14px 14px; display:flex; flex-direction:column; gap:8px; flex:1}
    .cbody h3{line-height:1.18; font-family: Georgia, serif; font-weight:900; letter-spacing:-.015em}
    .cbody h3 a{color:var(--ink); text-decoration:none; font-size:18px; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden}
    .cbody h3 a:hover{text-decoration:underline; text-decoration-thickness:1.5px; text-underline-offset:3px}
    .card .sum{color:#2b2b2b; font-size:13.5px; line-height:1.55; text-align:justify; hyphens:auto; display:-webkit-box; -webkit-line-clamp:4; -webkit-box-orient:vertical; overflow:hidden; min-height:84px}
    .card .meta{margin-top:auto; padding-top:8px; border-top:1px solid var(--rule-faint); display:flex; align-items:center; justify-content:space-between; font-size:10px}
    footer{margin:32px 0 24px; padding:16px 0; border-top:3px double var(--rule); border-bottom:1px solid var(--rule);
      color:var(--muted); font-family: system-ui, sans-serif; font-size:11px; line-height:1.5; display:flex; flex-wrap:wrap; gap:10px 18px; justify-content:center; text-align:center}
    footer b{color:var(--ink)}
    @media (max-width: 1100px){ .grid{grid-template-columns: repeat(2, 1fr)} }
    @media (max-width: 900px){
      .hero{grid-template-columns:1fr}
      .hero-media img{min-height:240px}
      .mast-title{font-size:42px}
      .pill{position:static; margin:8px auto 0}
    }
    @media (max-width: 620px){
      .grid{grid-template-columns:1fr}
      .wrap{padding:0 12px}
      .mast-top{flex-direction:column; gap:4px}
      .tabs{flex-wrap:wrap}
    }
"""



def _card(it, idx):
    if it["img"]:
        media = (f'<div class="thumb-wrap"><img loading="lazy" decoding="async" referrerpolicy="no-referrer" class="thumb" src="{html.escape(it["img"])}" '
                 f'alt="{html.escape(it["title"])}" onerror="this.style.display=\'none\';this.parentElement.classList.add(\'thumb--broken\')"><span class="srcpill srcpill--over">{html.escape(it["source"])}</span></div>')
    else:
        media = f'<div class="thumb-wrap"><div class="thumb thumb--ph">📰</div><span class="srcpill srcpill--over">{html.escape(it["source"])}</span></div>'
    return f"""<article class="card">
      {media}
      <div class="cbody">
        <h3><a href="{html.escape(it["link"], quote=True)}" target="_blank" rel="noopener">{html.escape(it["title"])}</a></h3>
        <p class="sum">{html.escape(_shorten(it["summary"]))}</p>
        <p class="meta"><span>{_timeago(it["pub_int"])}</span><span class="dot2"></span><a class="readmore" href="{html.escape(it["link"], quote=True)}" target="_blank" rel="noopener">Read more →</a></p>
      </div>
    </article>"""


def render(globals_list, kenya_list, business_list=None, tech_list=None, sports_list=None, debug=False):
    business_list = business_list or []
    tech_list = tech_list or []
    sports_list = sports_list or []
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
    total = len(globals_list) + len(kenya_list) + len(business_list) + len(tech_list) + len(sports_list)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Global Digest — The International & Kenya Morning Paper</title>
<meta name="description" content="The {total} biggest stories today — World {len(globals_list)}, Kenya {len(kenya_list)}, Business {len(business_list)}, Tech {len(tech_list)}, Sports {len(sports_list)} — refreshed every morning.">
<meta property="og:title" content="Global Digest — Today's Top Stories">
<meta property="og:description" content="Newspaper-style front page — World, Kenya, Business, Technology, Sports — rebuilt daily.">
<meta property="og:type" content="website">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📰</text></svg>">
<style>{_CSS}</style>
</head>
<body>
  <div class="wrap">
    <header class="mast">
      <div class="mast-top"><span>VOL. I — NO. 1</span><span>{today_label} — Nairobi (EAT)</span><span>Price: Free · Edition {updated}</span></div>
      <div class="mast-title">The <span class="thin">·</span> Global Digest</div>
      <div class="mast-sub"><span><b>EST. 2026</b> — WORLD · KENYA · BUSINESS · TECH · SPORTS · {total} STORIES DAILY</span><span style="margin-left:auto; display:flex; align-items:center; gap:8px" class="pill"><i></i> Live · Updated {updated}</span></div>
    </header>

    <nav class="tabs" role="tablist" aria-label="News sections">
      <button class="tab" role="tab" aria-selected="true" data-filter="all">All <span>{total}</span></button>
      <button class="tab" role="tab" aria-selected="false" data-filter="world">World <span>{len(globals_list)}</span></button>
      <button class="tab" role="tab" aria-selected="false" data-filter="kenya">Kenya <span>{len(kenya_list)}</span></button>
      <button class="tab" role="tab" aria-selected="false" data-filter="business">Business <span>{len(business_list)}</span></button>
      <button class="tab" role="tab" aria-selected="false" data-filter="tech">Tech <span>{len(tech_list)}</span></button>
      <button class="tab" role="tab" aria-selected="false" data-filter="sports">Sports <span>{len(sports_list)}</span></button>
    </nav>

    <div id="sec-hero" data-section="hero">{hero_html}</div>

    <div id="sec-world" data-section="world">
      <div class="section"><div class="section-head"><h2>World</h2><span class="count">{len(globals_list)} stories — BBC · Guardian · NYT · DW · Al Jazeera</span></div></div>
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

    <footer>
      <span><b>Global Digest</b> — newspaper front page, rebuilt daily at {updated} ({today_label}).</span>
      <span>Sources: BBC, The Guardian, NY Times, DW, Al Jazeera, KBC, Kenyans.co.ke, Nairobi Wire, Capital FM.</span>
      <span>“Read more” opens the original publisher — no tracking. No paywall.</span>
    </footer>
  </div>
<script>
(function(){{
  const tabs=document.querySelectorAll('.tab');
  const secs={{hero:document.getElementById('sec-hero'),world:document.getElementById('sec-world'),kenya:document.getElementById('sec-kenya'),business:document.getElementById('sec-business'),tech:document.getElementById('sec-tech'),sports:document.getElementById('sec-sports')}};
  function setFilter(f){{
    tabs.forEach(t=>t.setAttribute('aria-selected', String(t.dataset.filter===f)));
    Object.keys(secs).forEach(k=>{{ secs[k].style.display=''; }});
    if(f==='all'){{}}
    else if(f==='world'){{ secs.kenya.style.display='none'; secs.business.style.display='none'; secs.tech.style.display='none'; secs.sports.style.display='none'; }}
    else if(f==='kenya'){{ secs.hero.style.display='none'; secs.world.style.display='none'; secs.business.style.display='none'; secs.tech.style.display='none'; secs.sports.style.display='none'; }}
    else if(f==='business'){{ secs.hero.style.display='none'; secs.world.style.display='none'; secs.kenya.style.display='none'; secs.tech.style.display='none'; secs.sports.style.display='none'; }}
    else if(f==='tech'){{ secs.hero.style.display='none'; secs.world.style.display='none'; secs.kenya.style.display='none'; secs.business.style.display='none'; secs.sports.style.display='none'; }}
    else if(f==='sports'){{ secs.hero.style.display='none'; secs.world.style.display='none'; secs.kenya.style.display='none'; secs.business.style.display='none'; secs.tech.style.display='none'; }}
    window.scrollTo({{top:0, behavior:'smooth'}});
  }}
  tabs.forEach(t=>t.addEventListener('click', ()=>setFilter(t.dataset.filter)));
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    build()