#!/usr/bin/env python3
"""BCS Daily Preparation (Free Edition) - daily collector v2.

Runs in GitHub Actions (stdlib only, no pip installs).

Sources:
- Direct publisher RSS for The Daily Star and Prothom Alo (verified
  feeds). Their links are direct publisher URLs, so the full article
  text is extracted where the page is freely accessible. No
  paywall/login bypass of any kind.
- Google News RSS site feeds for the other 9 papers (excerpt + link;
  Google News links cannot be resolved programmatically).
- Google News top stories for Bangladesh (en + bn) as Current Affairs.

Writes data/articles.json (editorials + news, deduped).
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
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}

# name, lang, direct feed url, url fragments that mark opinion/editorial
# (TBS's rss.xml turned out to be a sports-heavy partial feed with no
# opinion items, so TBS stays on Google News like the other papers.)
DIRECT = [
    ("The Daily Star", "en", "https://www.thedailystar.net/opinion/rss.xml",
     ("/opinion/",)),
    ("প্রথম আলো", "bn", "https://www.prothomalo.com/feed",
     ("/opinion/",)),
]

GN_PAPERS = [
    ("The Business Standard", "en", "site:tbsnews.net thoughts OR opinion"),
    ("Dhaka Tribune", "en", "site:dhakatribune.com editorial OR opinion"),
    ("New Age", "en", "site:newagebd.net editorial OR opinion"),
    ("The Financial Express", "en", "site:thefinancialexpress.com.bd editorial OR opinion"),
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


# Paragraphs matching these are page junk, not article text:
# navigation menus, photo/visual credits, author blocks, comment and
# related-news footers. TRUNC markers end the article: everything from
# the first such paragraph onward is dropped.
JUNK_PATTERNS = (
    "main navigation", "file visual", "visual:", "photo:", "photo :",
    "ছবি:", "ছবি :", "can be reached at", "views expressed in this article",
    "follow the daily star opinion on facebook",
)
TRUNC_PATTERNS = ("click to comment", "comments policy", "related news")


def clean_full_text(text):
    if not text:
        return None
    paras = text.split("\n\n")
    if len(paras) == 1 and len(paras[0]) > 2000:
        # Single-block body (e.g. JSON-LD): only reject if it STARTS
        # with junk; mid-text pattern hits must not kill the article.
        head = paras[0][:200].lower()
        if any(j in head for j in JUNK_PATTERNS):
            return None
        return text if len(text) > 600 else None
    out, seen = [], set()
    for p in paras:
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) < 40:
            continue
        low = p.lower()
        if any(t in low for t in TRUNC_PATTERNS):
            break
        if any(j in low for j in JUNK_PATTERNS):
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    body = "\n\n".join(out)
    return body if len(body) > 600 else None


def extract_full_text(page_html):
    # Remove non-article regions first (nav/header/footer/aside/figure
    # hold menus, photo captions and other junk, not article text).
    page_html = re.sub(
        r"(?is)<(nav|header|footer|aside|figure|figcaption)[^>]*>.*?</\1>",
        " ", page_html)
    m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', page_html)
    if m:
        try:
            body = json.loads('"' + m.group(1) + '"')
            body = clean_full_text(re.sub(r"\s+", " ", body).strip())
            if body:
                return body
        except Exception:
            pass
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page_html)
    cleaned = []
    for p in paras:
        t = strip_tags(p)
        if len(t) >= 40:
            cleaned.append(t)
    return clean_full_text("\n\n".join(cleaned))


def extract_words(text, limit=12):
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    freq, first_sent = {}, {}
    for s in sentences:
        for w in re.findall(r"[A-Za-z][a-z]{6,}", s):
            lw = w.lower()
            if lw in STOP:
                continue
            freq[lw] = freq.get(lw, 0) + 1
            first_sent.setdefault(lw, s.strip())
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"w": w, "sentence": first_sent[w][:300]} for w, _ in ranked[:limit]]


def item_date(pub):
    try:
        return parsedate_to_datetime(pub).astimezone(timezone.utc).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def _field(block, tag):
    m = re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), block, re.S)
    if not m:
        return ""
    v = m.group(1).strip()
    v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.S)
    return v.strip()


def parse_rss(xml_bytes):
    """Yield dicts with title/link/date/desc/content/source.

    Regex-based on purpose: some publisher feeds (e.g. The Daily Star)
    are malformed XML (raw entities, unescaped HTML) that a strict
    XML parser rejects.
    """
    text = xml_bytes.decode("utf-8", "ignore")
    for block in re.findall(r"<item[^>]*>(.*?)</item>", text, re.S):
        title = strip_tags(_field(block, "title"))
        title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()  # GN suffix
        link = _field(block, "link")
        desc = strip_tags(_field(block, "description"))
        content = strip_tags(_field(block, "content:encoded"))
        source = strip_tags(_field(block, "source"))
        if title and link:
            yield {"title": title, "url": link,
                   "date": item_date(_field(block, "pubDate")),
                   "excerpt": desc[:700], "content": content,
                   "source": source}


def gn_feed_url(query, lang):
    q = urllib.parse.quote(query)
    if lang == "bn":
        return (f"https://news.google.com/rss/search?q={q}"
                "&hl=bn&gl=BD&ceid=BD:bn")
    return (f"https://news.google.com/rss/search?q={q}"
            "&hl=en-US&gl=BD&ceid=BD:en")


def collect_direct(name, lang, feed, fragments, seen, seen_titles):
    out = []
    try:
        xml_bytes, _ = fetch(feed)
        items = list(parse_rss(xml_bytes))
    except Exception as e:
        print(f"FEED FAIL {name}: {e}", file=sys.stderr, flush=True)
        return out
    picked = [it for it in items
              if any(f in it["url"] for f in fragments)]
    if len(picked) < 3:  # fall back to keyword match in title
        kw = ("editorial", "opinion", "thoughts", "সম্পাদকীয়", "মতামত")
        picked = [it for it in items
                  if any(f in it["url"] for f in fragments)
                  or any(k in it["title"].lower() for k in kw)]
    for it in picked[:8]:
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()
        if it["url"] in seen or key in seen_titles:
            continue
        seen.add(it["url"])
        seen_titles.add(key)
        full = it["content"] if len(it["content"]) > 900 else None
        if not full:
            try:
                raw, final_url = fetch(it["url"], timeout=10)
                if "news.google.com" not in final_url:
                    full = extract_full_text(raw.decode("utf-8", "ignore"))
            except Exception:
                full = None
        text_for_words = full or (it["excerpt"] if lang == "en" else None)
        out.append({"paper": name, "lang": lang, "kind": "editorial",
                    "title": it["title"], "date": it["date"], "url": it["url"],
                    "excerpt": it["excerpt"], "fullText": full,
                    "hasFullText": bool(full),
                    "words": extract_words(text_for_words) if lang == "en" else []})
    print(f"{name} (direct): {len(out)} new", flush=True)
    return out


def collect_gn(name, lang, query, seen, seen_titles):
    out = []
    try:
        xml_bytes, _ = fetch(gn_feed_url(query, lang))
        items = list(parse_rss(xml_bytes))
    except Exception as e:
        print(f"FEED FAIL {name}: {e}", file=sys.stderr, flush=True)
        return out
    for it in items[:8]:
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()
        if it["url"] in seen or key in seen_titles:
            continue
        seen.add(it["url"])
        seen_titles.add(key)
        out.append({"paper": name, "lang": lang, "kind": "editorial",
                    "title": it["title"], "date": it["date"], "url": it["url"],
                    "excerpt": it["excerpt"], "fullText": None,
                    "hasFullText": False,
                    "words": extract_words(it["excerpt"]) if lang == "en" else []})
    print(f"{name}: {len(out)} new", flush=True)
    return out


def collect_news(lang, seen, seen_titles):
    out = []
    url = ("https://news.google.com/rss?hl=bn&gl=BD&ceid=BD:bn" if lang == "bn"
           else "https://news.google.com/rss?hl=en-US&gl=BD&ceid=BD:en")
    try:
        xml_bytes, _ = fetch(url)
        items = list(parse_rss(xml_bytes))
    except Exception as e:
        print(f"NEWS FAIL {lang}: {e}", file=sys.stderr, flush=True)
        return out
    for it in items[:14]:
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()
        if it["url"] in seen or key in seen_titles:
            continue
        seen.add(it["url"])
        seen_titles.add(key)
        excerpt = it["excerpt"] if len(it["excerpt"]) >= 40 else ""
        out.append({"paper": it["source"] or "Google News", "lang": lang,
                    "kind": "news", "title": it["title"], "date": it["date"],
                    "url": it["url"], "excerpt": excerpt, "fullText": None,
                    "hasFullText": False, "words": []})
    print(f"News ({lang}): {len(out)} new", flush=True)
    return out


def main():
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "articles.json")
    existing = []
    if os.path.exists(out_path):
        try:
            existing = json.load(open(out_path, encoding="utf-8")).get("articles", [])
        except Exception:
            existing = []
    # Transition cleanup: drop old Google-News versions of the 3 direct
    # papers so they are re-collected with direct links + full text.
    direct_names = {d[0] for d in DIRECT}
    existing = [a for a in existing
                if not (a["paper"] in direct_names
                        and "news.google.com" in a["url"])]
    # Maintenance: re-clean stored full texts (older extractions carry
    # navigation/photo/author junk) and rebuild their word lists.
    for a in existing:
        if a.get("fullText"):
            cleaned = clean_full_text(a["fullText"])
            a["fullText"] = cleaned
            a["hasFullText"] = bool(cleaned)
            if cleaned and a["lang"] == "en":
                a["words"] = extract_words(cleaned)
    seen = {a["url"] for a in existing}
    seen_titles = {re.sub(r"\W+", " ", a["title"].lower()).strip()
                   for a in existing}
    new_items = []
    for name, lang, feed, frags in DIRECT:
        new_items += collect_direct(name, lang, feed, frags, seen, seen_titles)
    for name, lang, query in GN_PAPERS:
        new_items += collect_gn(name, lang, query, seen, seen_titles)
    for lang in ("en", "bn"):
        new_items += collect_news(lang, seen, seen_titles)
    all_articles = new_items + existing
    # Keep the archive focused: drop anything older than 45 days
    # (search feeds sometimes resurface years-old items).
    from datetime import date as _date, timedelta as _td
    cutoff = (_date.today() - _td(days=45)).isoformat()
    all_articles = [a for a in all_articles if a["date"] >= cutoff]
    all_articles.sort(key=lambda a: a["date"], reverse=True)
    all_articles = all_articles[:600]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    payload = {"updated": datetime.now(timezone.utc).isoformat(),
               "articles": all_articles}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    eds = [a for a in all_articles if a["kind"] == "editorial"]
    news = [a for a in all_articles if a["kind"] == "news"]
    print(f"TOTAL {len(all_articles)} ({len(eds)} editorials, {len(news)} news), "
          f"{sum(1 for a in all_articles if a['hasFullText'])} with full text, "
          f"{len(new_items)} new this run", flush=True)


if __name__ == "__main__":
    main()
