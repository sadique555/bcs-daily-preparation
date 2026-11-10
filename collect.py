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


# International editorials and think-tank analysis (added 2026-11-10 at
# the user's request): world opinion pages + leading think tanks, kept
# BCS-IR relevant by the same gate as everything else.
INTL_GN = [
    ("The Guardian — Opinion", "en", "site:theguardian.com/commentisfree when:30d"),
    ("Project Syndicate", "en", "site:project-syndicate.org/commentary when:30d"),
    ("Al Jazeera — Opinion", "en", "site:aljazeera.com/opinions when:30d"),
    ("Foreign Policy", "en", "site:foreignpolicy.com analysis when:30d"),
    ("The Hindu — Opinion", "en", "site:thehindu.com/opinion when:30d"),
    ("Dawn — Opinion", "en", "site:dawn.com opinion when:30d"),
    ("Chatham House", "en", "site:chathamhouse.org when:30d"),
    ("Brookings", "en", "site:brookings.edu when:30d"),
    ("Carnegie Endowment", "en", "site:carnegieendowment.org when:30d"),
    ("International Crisis Group", "en", "site:crisisgroup.org when:30d"),
    ("CSIS", "en", "site:csis.org when:30d"),
    ("Council on Foreign Relations", "en", "site:cfr.org when:30d"),
    ("Atlantic Council", "en", "site:atlanticcouncil.org when:30d"),
    ("Stimson Center", "en", "site:stimson.org when:30d"),
    ("Lowy Institute", "en", "site:lowyinstitute.org when:30d"),
    ("ORF — Observer Research Foundation", "en", "site:orfonline.org when:30d"),
    # Topic-targeted think-tank searches (2026-11-10): the generic
    # per-institute queries surface only each institute's newest few
    # pieces, which buried Iran / Middle East analysis entirely.
    ("International Crisis Group", "en", "site:crisisgroup.org Iran OR Middle East OR Gaza when:30d"),
    ("Chatham House", "en", "site:chathamhouse.org Iran OR Middle East when:30d"),
    ("CSIS", "en", "site:csis.org Iran OR Middle East when:30d"),
    ("Atlantic Council", "en", "site:atlanticcouncil.org Iran war OR Middle East when:30d"),
    ("Brookings", "en", "site:brookings.edu Iran OR Gaza OR Israel when:30d"),
    ("Carnegie Endowment", "en", "site:carnegieendowment.org Iran OR Middle East when:30d"),
    ("Council on Foreign Relations", "en", "site:cfr.org Iran OR Middle East when:30d"),
    ("Stimson Center", "en", "site:stimson.org Iran OR Middle East when:30d"),
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
    # Unescape FIRST: some feeds entity-escape their HTML
    # (&lt;a href=...&gt;), so stripping tags before unescaping would
    # leave the raw tags in the text after the final unescape.
    text = unescape(html or "")
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    # A tag cut off mid-way (old runs truncated excerpts at 700 chars)
    # leaves a fragment like '<a href=" target=' with no closing '>'.
    text = re.sub(r"<[^>]*$", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_excerpt(text, title="", source=""):
    """Return only real article text, or "" when the feed gave no text.

    Google News RSS descriptions are often NOT article text at all:
    they are the headline + source name, a numbered list of related
    headlines, or a photo caption fragment such as "(File Photo:
    Reuters)". Showing those as the article body is what the user
    complained about ("sudhu text gulai ase, photo-r description bad").
    """
    t = strip_tags(text or "")
    # A <ol>/<ul> list of related headlines is not article text, even
    # though stripping its tags leaves headline words behind.
    if len(re.findall(r"(?i)<li[\s>]", unescape(text or ""))) >= 2:
        return ""
    # Drop any leftover link / image URL (its slug fragments otherwise
    # end up as fake "words", e.g. "qkxhekpk").
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)
    # Drop photo-caption fragments wherever they appear.
    t = re.sub(r"(?i)\([^)]*(file photo|photo credit|photo:)[^)]*\)", " ", t)
    t = re.sub(r"(?i)\b(file photo|photo|ছবি)\s*[:：]\s*[^.;।]{0,120}", " ", t)
    # Google News joins related coverage with " | " — keep only the
    # article's own part before the first separator.
    t = t.split(" | ")[0].strip()
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    norm = lambda s: re.sub(r"\W+", " ", (s or "").lower()).strip()
    # Title (+ source) only, no article text. The source name in the
    # feed often differs from the paper label ("The Daily Ittefaq"
    # vs "দৈনিক ইত্তেফাক"), so also drop a title followed by a short
    # sourcelike tail with no sentence punctuation.
    body = norm(t)
    if body == norm(title) or body == norm(title + " " + source) \
            or body == norm(source + " " + title):
        return ""
    tn = norm(title)
    # Headline-first check: Google News descriptions open with the
    # article's own headline and then append related headlines
    # ("<title> <source> <other headline> <source> ..."). If the
    # excerpt's first words are the title's first words, nothing after
    # them is this article's text either, so drop the whole thing.
    # Real standfirsts (Daily Star / Prothom Alo direct feeds) do not
    # repeat the headline, so they are unaffected.
    bw_list = body.split()
    tw_list = tn.split()
    n = min(8, len(bw_list), len(tw_list))
    if n >= 4 and len(set(bw_list[:n]) & set(tw_list[:n])) >= max(4, int(n * 0.75)):
        return ""
    if tn and body.startswith(tn):
        tail = body[len(tn):].strip()
        if len(tail) <= 40 and not any(p in tail for p in ".!?,;:।"):
            return ""
    # Fuzzy title match: Google News often rewords/truncates the
    # headline in the description ("...meet Trump Monday" vs the title
    # "...meet with Trump Monday over offer"). If ≥80% of the excerpt's
    # words are title words and it is barely longer than the title,
    # it is the headline again, not article text.
    tw = set(tn.split())
    bw = set(body.split())
    if tw and bw and len(bw & tw) / len(bw) >= 0.8 \
            and len(body) <= len(tn) + 50:
        return ""
    if len(t) < len(title) + 10 and norm(title) and norm(title) in body:
        return ""
    # Numbered list of related headlines ("1. ... 2. ... 3. ...").
    if len(re.findall(r"(?:^|\s)\d{1,2}\.\s", t)) >= 2:
        return ""
    return t


# Paragraphs matching these are page junk, not article text:
# navigation menus, photo/visual credits, author blocks, comment and
# related-news footers. TRUNC markers end the article: everything from
# the first such paragraph onward is dropped.
JUNK_PATTERNS = (
    "powered by: rsi lab", "thedailystar.net",
    "unauthorized use or reproduction",
    "main navigation", "file visual", "visual:", "photo:", "photo :",
    "ছবি:", "ছবি :", "can be reached at", "views expressed in this article",
    "follow the daily star opinion on facebook",
)
TRUNC_PATTERNS = ("click to comment", "comments policy", "related news",
                "powered by: rsi lab", "unauthorized use or reproduction")


MENU_LABELS = ("e-paper", "today's news", "today\u2019s news", "politics",
               "governance", "crime and justice", "business", "economy",
               "sports", "cricket", "football", "lifestyle", "entertainment",
               "opinion", "editorial", "slow reads", "my dhaka",
               "geopolitical insights", "big picture", "campus life",
               "pop culture", "world", "technology", "education")


def _is_menu_paragraph(p):
    low = p.lower()
    return sum(1 for m in MENU_LABELS if m in low) >= 5


def _strip_site_chrome(text):
    # The Daily Star full-page scrape glues the header menu and the
    # copyright footer BEFORE the article body in one block. The footer
    # signature ends with "liable to legal action." — everything up to
    # there is chrome, not article.
    sig = text.find("unauthorized use or reproduction")
    if sig != -1:
        end = text.find("liable to legal action.", sig)
        if end != -1:
            rest = text[end + len("liable to legal action."):].strip()
            if len(rest) > 600:
                return rest
            return text[:sig].rstrip()
    # Footer at the end (body first): cut from the © line.
    c = text.find("\u00a9")
    if c != -1 and "thedailystar" in text[c:c + 200].lower():
        return text[:c].rstrip()
    return text


def clean_full_text(text):
    if not text:
        return None
    text = _strip_site_chrome(text)
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
        if _is_menu_paragraph(p):
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
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
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
        # Pass the RAW description to clean_excerpt: it needs the markup
        # to recognise a related-headlines <ol>/<li> list, and it strips
        # tags itself (entity-escaped HTML included).
        desc = _field(block, "description")
        content = strip_tags(_field(block, "content:encoded"))
        source = strip_tags(_field(block, "source"))
        if title and link:
            yield {"title": title, "url": link,
                   "date": item_date(_field(block, "pubDate")),
                   "excerpt": clean_excerpt(desc, title, source),
                   "content": content,
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


def collect_gn(name, lang, query, seen, seen_titles, limit=8):
    out = []
    try:
        xml_bytes, _ = fetch(gn_feed_url(query, lang))
        items = list(parse_rss(xml_bytes))
    except Exception as e:
        print(f"FEED FAIL {name}: {e}", file=sys.stderr, flush=True)
        return out
    for it in items[:limit]:
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


# ---- BCS relevance gate -------------------------------------------------
# The user wants ONLY BCS-useful news: Bangladesh affairs for the BCS
# written exam + international-relations material. Sports, entertainment
# and police-blotter items are hard-dropped; news must match a relevance
# keyword, editorials pass unless hard-dropped.
REL_EN = ["economy", "economic", "budget", "inflation", "gdp",
          "remittance", "export", "trade", "investment", "energy",
          "climate", "environment", "river", "agriculture",
          "education", "health", "poverty", "urban", "governance",
          "constitution", "reform", "parliament", "election",
          "judiciary", "corruption", "diplomacy", "diplomat",
          "treaty", "border", "refugee", "bilateral", "population",
          "development", "government", "political", "politics",
          "democracy", "ministry", "prime minister", "premier",
          "crisis", "security", "heat", "garment", "textile",
          "industry", "labour", "labor", "workers", "wages",
          "foreign policy", "foreign affairs", "foreign minister",
          "foreign secretary", "foreign aid", "water sharing",
          "tariff", "military", "humanitarian", "conflict",
          "ceasefire", "sanction",
          "global economy", "summit", "united nations",
          "security council", "geopolit"]
EN_BD_ANCHOR = ["bangladesh", "dhaka", "rohingya", "bay of bengal"]
EN_IR_ANCHOR = ["united nations", "security council", "geopolit",
                "world bank", "imf", "wto", "nato", "g7 ", "g20",
                "brics", "asean", "saarc", "bimstec", "middle east",
                "ukraine", "gaza", "ceasefire", "sanctions",
                "indo-pacific", "climate summit", "cop29", "cop30",
                "cop31", "trade war", "south asia", "iran", "israel",
                "russia", "taiwan", "north korea", "south china sea",
                "red sea", "european union", "opec", "coup", "pentagon",
                "kremlin", "nuclear", "missile"]
REL_BN_STRONG = ["অর্থনীতি", "বাজেট", "মুদ্রাস্ফীতি", "মূল্যস্ফীতি",
                 "রেমিট্যান্স", "রপ্তানি", "আমদানি", "বাণিজ্য",
                 "বিনিয়োগ", "জ্বালানি", "বিদ্যুৎ", "জলবায়ু", "পরিবেশ",
                 "নদী", "কৃষি", "শিক্ষা", "স্বাস্থ্য", "দারিদ্র্য",
                 "উন্নয়ন", "সুশাসন", "সংবিধান", "সংস্কার", "সংসদ",
                 "নির্বাচন", "বিচার বিভাগ", "দুর্নীতি", "কূটনীতি",
                 "কূটনৈতিক", "পররাষ্ট্র", "চুক্তি", "সীমান্ত",
                 "জাতিসংঘ", "আন্তর্জাতিক", "ভূরাজনীতি", "জনসংখ্যা",
                 "পানিবণ্টন", "বৈদেশিক", "প্রবাসী", "ব্যাংক", "রিজার্ভ",
                 "রাজস্ব", "মুনাফা", "শেয়ারবাজার", "পুঁজিবাজার",
                 "মুক্তিযুদ্ধ", "শ্রম", "শিল্প", "নগর", "গণতন্ত্র",
                 "যুদ্ধ", "সংকট", "সামরিক", "রাজনীতি", "ইউক্রেন",
                 "গাজা", "ইরান", "রাশিয়া", "ন্যাটো", "শান্তি"]
BN_ANCHOR = ["বাংলাদেশ", "ঢাকা", "রোহিঙ্গা", "পদ্মা"]
HARD_EN = ["cricket", "football", "premier league", "ipl", "t20 ",
           "world cup", "fifa", "uefa", "tennis", "test match",
           "semifinal", "semi-final", "innings", "wicket", "batting",
           "bowling", "striker", "goalkeeper",
           "cinema", "movie star", "film festival", "actor ",
           "actress", "celebrity", "singer ", "concert", "reality show",
           "miss universe", "accident", "road crash", "bus crash",
           "fire breaks out", "fire incident", "robbery",
           "murder case", "rape case", "horoscope", "lottery result"]
HARD_BN = ["ক্রিকেট", "ফুটবল", "খেলোয়াড়", "টুর্নামেন্ট", "বিশ্বকাপ",
           "সেমিফাইনাল", "ইনিংস", "উইকেট", "ব্যাটিং", "বোলিং",
           "টেস্টে", "ওয়ানডে", "টি-টোয়েন্টি", "গোল করে", "হকি",
           "সিনেমা", "চলচ্চিত্র", "অভিনেতা", "অভিনেত্রী", "গায়ক",
           "গায়িকা", "ধারাবাহিক নাটক", "বিনোদন", "দুর্ঘটনা",
           "অগ্নিকাণ্ড", "ডাকাতি", "রাশিফল", "লটারি", "জুয়া"]


def _hit(t, words):
    return any(w in t for w in words)


def is_relevant(title, kind):
    t = title.lower()
    if _hit(t, HARD_EN) or _hit(title, HARD_BN):
        return False
    if kind != "news":
        return True
    if any("\u0980" <= ch <= "\u09ff" for ch in title):
        hits = sum(1 for w in REL_BN_STRONG if w in title)
        return hits >= 2 or (_hit(title, BN_ANCHOR) and hits >= 1)
    topical = _hit(t, REL_EN)
    anchor = _hit(t, EN_BD_ANCHOR) or _hit(t, EN_IR_ANCHOR)
    return topical and anchor


NEWS_QUERIES = {
    "en": ["Bangladesh economy policy", "Bangladesh foreign policy diplomacy",
           "Bangladesh climate change environment",
           "Bangladesh constitution reform governance",
           "geopolitics international relations United Nations",
           "world politics crisis war diplomacy summit",
           "Middle East conflict ceasefire humanitarian",
           "US China Russia geopolitics trade war tariffs",
           "global economy IMF World Bank outlook"],
    "bn": ["বাংলাদেশ অর্থনীতি নীতি", "বাংলাদেশ পররাষ্ট্রনীতি কূটনীতি",
           "জলবায়ু পরিবর্তন পরিবেশ বাংলাদেশ", "সংবিধান সংস্কার সুশাসন",
           "আন্তর্জাতিক সম্পর্ক ভূরাজনীতি",
           "আন্তর্জাতিক রাজনীতি সংকট যুদ্ধ কূটনীতি",
           "মধ্যপ্রাচ্য যুদ্ধবিরতি সংকট",
           "বিশ্ব অর্থনীতি বাণিজ্য যুদ্ধ"],
}


def _take_news(items, lang, seen, seen_titles, out, limit):
    for it in items:
        if len(out) >= limit:
            break
        if not is_relevant(it["title"], "news"):
            continue
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
    return out


def collect_news(lang, seen, seen_titles):
    out = []
    url = ("https://news.google.com/rss?hl=bn&gl=BD&ceid=BD:bn" if lang == "bn"
           else "https://news.google.com/rss?hl=en-US&gl=BD&ceid=BD:en")
    try:
        xml_bytes, _ = fetch(url)
        _take_news(list(parse_rss(xml_bytes))[:40], lang, seen,
                   seen_titles, out, 10)
    except Exception as e:
        print(f"NEWS FAIL {lang}: {e}", file=sys.stderr, flush=True)
    for q in NEWS_QUERIES[lang]:
        try:
            xml_bytes, _ = fetch(gn_feed_url(q + " when:7d", lang))
            _take_news(list(parse_rss(xml_bytes))[:12], lang, seen,
                       seen_titles, out, len(out) + 6)
        except Exception as e:
            print(f"NEWS QUERY FAIL {q}: {e}", file=sys.stderr, flush=True)
    print(f"News ({lang}): {len(out)} new", flush=True)
    return out


def collect_world_news(seen, seen_titles):
    """Recent international news (crises, politics, diplomacy) for the
    Current Affairs section — added 2026-11-10 at the user's request.
    US-edition top stories + forced-fresh GN searches, passed through
    the same BCS relevance gate as all other news."""
    out = []
    try:
        xml_bytes, _ = fetch(
            "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en")
        _take_news(list(parse_rss(xml_bytes))[:40], "en", seen,
                   seen_titles, out, 8)
    except Exception as e:
        print(f"WORLD FAIL: {e}", file=sys.stderr, flush=True)
    for q in ("world crisis politics war diplomacy when:2d",
              "ceasefire summit nuclear talks conflict when:2d"):
        try:
            xml_bytes, _ = fetch(gn_feed_url(q, "en"))
            _take_news(list(parse_rss(xml_bytes))[:12], "en", seen,
                       seen_titles, out, len(out) + 5)
        except Exception as e:
            print(f"WORLD QUERY FAIL {q}: {e}", file=sys.stderr, flush=True)
    print(f"World news: {len(out)} new", flush=True)
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
    # Also re-clean stored excerpts: older runs kept Google News
    # title-only / related-headlines / photo-caption text as "excerpt".
    for a in existing:
        if a.get("excerpt") is not None:
            a["excerpt"] = clean_excerpt(a.get("excerpt") or "",
                                         a.get("title") or "",
                                         a.get("paper") or "")
        if a.get("fullText"):
            cleaned = clean_full_text(a["fullText"])
            a["fullText"] = cleaned
            a["hasFullText"] = bool(cleaned)
            if cleaned and a["lang"] == "en":
                a["words"] = extract_words(cleaned)
        elif a.get("lang") == "en":
            # Excerpt-only item: rebuild words from the cleaned excerpt
            # (junk excerpts now clean to "", so their fake words go too).
            a["words"] = extract_words(a.get("excerpt") or "")
    seen = {a["url"] for a in existing}
    seen_titles = {re.sub(r"\W+", " ", a["title"].lower()).strip()
                   for a in existing}
    new_items = []
    for name, lang, feed, frags in DIRECT:
        new_items += collect_direct(name, lang, feed, frags, seen, seen_titles)
    for name, lang, query in GN_PAPERS:
        new_items += collect_gn(name, lang, query, seen, seen_titles)
    for name, lang, query in INTL_GN:
        new_items += collect_gn(name, lang, query, seen, seen_titles, limit=4)
    for lang in ("en", "bn"):
        new_items += collect_news(lang, seen, seen_titles)
    new_items += collect_world_news(seen, seen_titles)
    all_articles = new_items + existing
    # BCS relevance gate: drop non-BCS news (and hard-dropped editorials),
    # including items stored before this filter existed.
    before = len(all_articles)
    all_articles = [a for a in all_articles
                    if is_relevant(a["title"], a["kind"])]
    print(f"Relevance gate: dropped {before - len(all_articles)} items",
          flush=True)
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
