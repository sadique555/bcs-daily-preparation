#!/usr/bin/env python3
"""BCS Daily Preparation (Free Edition) - daily collector.

Runs in GitHub Actions (stdlib only, no pip installs).
Fetches editorials/opinion items for 11 Bangladeshi newspapers via
Google News RSS site feeds, tries to extract the full article text
where the publisher page is freely accessible, dedupes, and writes
data/articles.json. No paywall/login bypass of any kind.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"}

PAPERS = [
    # name, lang, google-news query
    ("The Daily Star", "en", "site:thedailystar.net editorial OR opinion"),
    ("Dhaka Tribune", "en", "site:dhakatribune.com editorial OR opinion"),
    ("New Age", "en", "site:newagebd.net editorial OR opinion"),
    ("The Financial Express", "en", "site:thefinancialexpress.com.bd editorial OR opinion"),
    ("The Business Standard", "en", "site:tbsnews.net thoughts OR opinion"),
    ("প্রথম আলো", "bn", "site:prothomalo.com মতামত OR সম্পাদকীয়"),
    ("দৈনিক ইত্তেফাক", "bn", "site:ittefaq.com.bd মতামত OR সম্পাদকীয়"),
    ("সমকাল", "bn", "site:samakal.com মতামত OR সম্পাদকীয়"),
    ("কালের কণ্ঠ", "bn", "site:kalerkantho.com মতামত OR সম্পাদকীয়"),
    ("যুগান্তর", "bn", "site:jugantor.com সম্পাদকীয় OR মতামত"),
    ("বাংলাদেশ প্রতিদিন", "bn", "site:bd-pratidin.com সম্পাদকীয় OR মতামত"),
]

STOP = set("""the and for are but not you all any can her was one our out day get has him
his how man new now old see two way who boy did its let put say she too use that with have
this will your from they know want been good much some time very when come here just like
long make many over such take than them well were about above after again against among
because become before being below between both could does doing down during each few from
further had has have having into itself more most other our ours out over own same should
so some such than that the their theirs them then there these they this those through too
under until very was we were what when where which while who whom why with would you your
government bangladesh people country political party national development economic
""".split())


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl()


def strip_tags(html):
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def extract_full_text(page_html):
    # 1) JSON-LD articleBody (e.g. Prothom Alo embeds the full body)
    m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', page_html)
    if m:
        try:
            body = json.loads('"' + m.group(1) + '"')
            body = re.sub(r"\s+", " ", body).strip()
            if len(body) > 600:
                return body
        except Exception:
            pass
    # 2) paragraph extraction
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page_html)
    cleaned = []
    for p in paras:
        t = strip_tags(p)
        if len(t) >= 40:
            cleaned.append(t)
    body = "\n\n".join(cleaned)
    if len(body) > 900:
        return body
    return None


def extract_words(text, limit=12):
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    freq = {}
    first_sent = {}
    for s in sentences:
        for w in re.findall(r"[A-Za-z][a-z]{6,}", s):
            lw = w.lower()
            if lw in STOP:
                continue
            freq[lw] = freq.get(lw, 0) + 1
            first_sent.setdefault(lw, s.strip())
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"w": w, "sentence": first_sent[w][:300]} for w, _ in ranked[:limit]]


def feed_url(query, lang):
    q = urllib.parse.quote(query)
    if lang == "bn":
        return (f"https://news.google.com/rss/search?q={q}"
                "&hl=bn&gl=BD&ceid=BD:bn")
    return (f"https://news.google.com/rss/search?q={q}"
            "&hl=en-US&gl=BD&ceid=BD:en")


def parse_feed(xml_bytes):
    items = []
    root = ET.fromstring(xml_bytes)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        # Google News titles end with " - <Source>"; strip that suffix
        title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = strip_tags(item.findtext("description") or "")
        try:
            dt = parsedate_to_datetime(pub)
            date = dt.astimezone(timezone.utc).date().isoformat()
        except Exception:
            date = datetime.now(timezone.utc).date().isoformat()
        if title and link:
            items.append({"title": title, "url": link,
                          "date": date, "excerpt": desc[:700]})
    return items


def main():
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "articles.json")
    existing = []
    if os.path.exists(out_path):
        try:
            existing = json.load(open(out_path, encoding="utf-8")).get("articles", [])
        except Exception:
            existing = []
    seen = {a["url"] for a in existing}
    seen_titles = {re.sub(r"\W+", " ", a["title"].lower()).strip() for a in existing}
    new_items = []
    for name, lang, query in PAPERS:
        try:
            xml_bytes, _ = fetch(feed_url(query, lang))
            items = parse_feed(xml_bytes)
        except Exception as e:
            print(f"FEED FAIL {name}: {e}", file=sys.stderr, flush=True)
            continue
        added = 0
        for it in items[:8]:
            key = re.sub(r"\W+", " ", it["title"].lower()).strip()
            if it["url"] in seen or key in seen_titles:
                continue
            seen.add(it["url"])
            seen_titles.add(key)
            # NOTE: Google News article links cannot be resolved to the
            # publisher page programmatically (Google serves an
            # interstitial; its decode RPC rejects outside calls), so
            # items stay excerpt + original link. The link itself opens
            # the publisher page normally in a browser.
            full = None
            text_for_words = full or (it["excerpt"] if lang == "en" else None)
            new_items.append({
                "paper": name, "lang": lang, "kind": "editorial",
                "title": it["title"], "date": it["date"], "url": it["url"],
                "excerpt": it["excerpt"],
                "fullText": full, "hasFullText": bool(full),
                "words": extract_words(text_for_words) if lang == "en" else [],
            })
            added += 1
        print(f"{name}: {added} new", flush=True)
    all_articles = new_items + existing
    all_articles.sort(key=lambda a: a["date"], reverse=True)
    all_articles = all_articles[:500]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    payload = {"updated": datetime.now(timezone.utc).isoformat(),
               "articles": all_articles}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"TOTAL {len(all_articles)} articles "
          f"({sum(1 for a in all_articles if a['hasFullText'])} with full text), "
          f"{len(new_items)} new this run", flush=True)


if __name__ == "__main__":
    main()
