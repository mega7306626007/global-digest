#!/usr/bin/env python3
"""Global Digest MVP — 25 stories, minimal newspaper, stdlib only."""
import datetime as dt, email.utils, html, json, os, re, sys, urllib.parse, urllib.request, xml.etree.ElementTree as ET

GLOBAL_COUNT = 15
KENYA_COUNT = 10
TIMEOUT = 6
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

FEEDS = [
    ("https://feeds.bbci.co.uk/news/world/rss.xml", "global", 4),
    ("https://www.theguardian.com/world/rss", "global", 3),
    ("https://www.aljazeera.com/xml/rss/all.xml", "global", 2),
    ("https://www.kbc.co.ke/feed/", "kenya", 4),
    ("https://nairobiwire.com/feed", "kenya", 3),
    ("https://www.kenyans.co.ke/rss.xml", "kenya", 2),
]

def _fetch(url):
    try:
        req=urllib.request.Request(url, headers={"User-Agent":USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", errors="replace")
    except: return None

def _local(t): return t.split("}")[-1]

def _parse(body):
    items=[]
    try: root=ET.fromstring(body)
    except: return items
    for node in root.iter():
        if _local(node.tag) not in ("item","entry"): continue
        title=link=summary=img=pub=""
        for c in node.iter():
            tag=_local(c.tag); txt=(c.text or "").strip()
            if tag=="title" and not title: title=txt
            elif tag=="link" and not link:
                link=txt
                if c.attrib.get("href"): link=c.attrib["href"]
            elif tag in ("description","summary","content") and not summary:
                summary=re.sub(r"<[^>]+>"," ",txt)
            elif tag in ("thumbnail","content") and not img and c.attrib.get("url"):
                img=c.attrib["url"]
            elif tag=="enclosure" and c.attrib.get("url") and not img and "image" in c.attrib.get("type",""):
                img=c.attrib["url"]
            elif tag.lower() in ("pubdate","published","updated"): pub=txt
        if "http" not in link:
            for a in node.findall(".//*{link}"):
                if a.attrib.get("href"): link=a.attrib["href"]; break
        if not title or not link.startswith("http"): continue
        pub_int=int(dt.datetime.now().timestamp())
        if pub:
            try:
                d=email.utils.parsedate_to_datetime(pub)
                pub_int=int(d.timestamp())
            except:
                try:
                    iso=pub.replace("Z","+00:00")
                    d=dt.datetime.fromisoformat(iso)
                    if d.tzinfo is None: d=d.replace(tzinfo=dt.timezone.utc)
                    pub_int=int(d.timestamp())
                except: pass
        # Upgrade BBC thumb
        if "ichef.bbci.co.uk" in img: img=re.sub(r"/\d+/", "/800/", img)
        items.append({"title":title,"link":link,"summary":re.sub(r"\s+"," ",summary).strip(),"img":img,"pub_int":pub_int})
    return items

def build():
    all_items=[]
    for url,kind,w in FEEDS:
        body=_fetch(url)
        if not body:
            print(f"skip {url}"); continue
        for i,it in enumerate(_parse(body)):
            it["kind"]=kind; it["score"]=w*100 - i*1.5; all_items.append(it)
        print(f"ok {url} -> {len(_parse(body))}")
    # dedupe
    uniq={}
    for it in all_items:
        k=re.sub(r"[^a-z0-9]+","",it["title"].lower())[:60]
        if not k: continue
        if k not in uniq or it["score"]>uniq[k]["score"]: uniq[k]=it
    ranked=list(uniq.values())
    gl=sorted([x for x in ranked if x["kind"]=="global"], key=lambda x:-x["score"])[:GLOBAL_COUNT]
    ke=sorted([x for x in ranked if x["kind"]=="kenya"], key=lambda x:-x["score"])[:KENYA_COUNT]
    # simple og fallback for missing images
    for it in gl+ke:
        if not it["img"]:
            try:
                page=_fetch(it["link"])
                m=re.search(r'property="og:image"[^>]+content="([^"]+)"', page or "")
                if m: it["img"]=m.group(1)
            except: pass
        if not it["img"]: it["img"]=""
    # render minimal newspaper
    today=dt.date.today()
    label=f"{today.strftime('%A')}, {today.day} {today.strftime('%B')} {today.year}"
    updated=dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).strftime("%H:%M EAT")
    def card(it):
        img=f'<img src="{html.escape(it["img"])}" alt="" style="width:100%;aspect-ratio:16/10;object-fit:cover;display:block;background:#f1f5f9">' if it["img"] else '<div style="aspect-ratio:16/10;background:#f1f5f9;display:grid;place-items:center">📰</div>'
        return f'<article style="background:#fff;border:1px solid #d8cfb8;border-top:2.5px solid #0a0a0a;padding:0;overflow:hidden"><div style="border-bottom:1px solid #e9e1ca">{img}</div><div style="padding:12px 14px"><h3 style="font-family:Georgia,serif;margin:0 0 6px;font-size:17px;line-height:1.25"><a href="{html.escape(it["link"])}" target="_blank" rel="noopener" style="color:#0a0a0a;text-decoration:none">{html.escape(it["title"])}</a></h3><p style="margin:0;color:#444;font-size:13.5px;line-height:1.5">{html.escape(it["summary"][:180])}…</p><p style="margin:8px 0 0;color:#888;font-size:11px">{html.escape(it["link"].split("/")[2])} · <a href="{html.escape(it["link"])}" target="_blank" style="color:#b91c1c;font-weight:700;text-decoration:none">Read more →</a></p></div></article>'
    hero=gl[0] if gl else None
    hero_html=""
    if hero:
        hero_html=f'<a href="{html.escape(hero["link"])}" target="_blank" rel="noopener" style="display:grid;grid-template-columns:1.2fr .8fr;gap:18px;background:#fff;border:1px solid #d8cfb8;border-top:3px solid #0a0a0a;padding:16px;text-decoration:none;color:inherit;margin-bottom:18px"><div><img src="{html.escape(hero["img"])}" style="width:100%;height:100%;min-height:280px;object-fit:cover;display:block"></div><div><div style="display:inline-block;background:#b91c1c;color:#fff;font-family:system-ui,sans-serif;font-size:10px;letter-spacing:.14em;padding:4px 8px">TOP STORY</div><h1 style="font-family:Georgia,serif;font-size:28px;line-height:1.1;margin:10px 0">{html.escape(hero["title"])}</h1><p style="color:#444;font-size:14px">{html.escape(hero["summary"][:220])}…</p><p style="color:#888;font-size:11px;margin-top:10px">Updated {updated}</p></div></a>'
        gl_cards="".join(card(it) for it in gl[1:])
    else:
        gl_cards="".join(card(it) for it in gl)
    ke_cards="".join(card(it) for it in ke)
    html_out=f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Global Digest MVP — 25 Stories</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#fdfaf3;color:#0a0a0a;font-family:Georgia,serif;line-height:1.6}} .wrap{{max-width:1100px;margin:0 auto;padding:0 16px}}
header{{border-top:4px solid #0a0a0a;border-bottom:1px solid #0a0a0a;padding:14px 0 10px;text-align:center;background:#fdfaf3}}
.mast{{font-size:42px;font-weight:900;letter-spacing:-.02em;text-transform:uppercase}} .sub{{font-family:system-ui,sans-serif;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#666;border-top:1px solid #d8cfb8;border-bottom:3px double #0a0a0a;padding:6px 0;margin-top:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}} h2{{font-size:22px;text-transform:uppercase;letter-spacing:-.02em;border-top:3px double #0a0a0a;padding-top:8px;margin:22px 0 10px}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr}} .hero{{grid-template-columns:1fr!important}} }}
</style></head><body><div class="wrap">
<header><div class="mast">The Global Digest <span style="font-size:11px;letter-spacing:.14em;background:#0a0a0a;color:#fff;padding:3px 7px;vertical-align:middle">MVP</span></div><div class="sub">{label} · {updated} · 25 stories · Auto 06:00 EAT</div></header>
<p style="text-align:center;font-family:system-ui,sans-serif;font-size:12px;color:#666;margin:10px 0">World & Kenya — minimal, fast, no JS required. <a href="https://mega7306626007.github.io/global-digest/" style="color:#b91c1c">Full 150 →</a></p>
{hero_html}
<h2>World — {len(gl)} stories</h2><div class="grid">{gl_cards}</div>
<h2>Kenya — {len(ke)} stories</h2><div class="grid">{ke_cards}</div>
<footer style="margin:24px 0;border-top:3px double #0a0a0a;padding:12px 0;color:#666;font-family:system-ui,sans-serif;font-size:11px;text-align:center">Global Digest MVP — built daily at {updated} · Sources: BBC, Guardian, KBC etc. · No tracking.</footer>
</div></body></html>"""
    os.makedirs("dist", exist_ok=True)
    open("dist/index.html","w",encoding="utf-8").write(html_out)
    open("dist/latest.json","w",encoding="utf-8").write(json.dumps({"generated":dt.datetime.now(dt.timezone.utc).isoformat(),"global":gl,"kenya":ke}, ensure_ascii=False, indent=2))
    print(f"Built MVP dist/index.html with {len(gl)} global + {len(ke)} Kenya")

if __name__=="__main__": build()
