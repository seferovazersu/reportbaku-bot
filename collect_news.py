"""
ReportBaku - 1-ci addım: RSS-dən xəbər toplamaq və seçmək.

Nə edir:
1. Hər RSS mənbəyini yoxlayır (canlıdır, ya yox) və nəticəni çap edir.
2. Son 24 saatın xəbərlərini toplayır.
3. Əvvəl seçilmiş xəbərləri (seen.json) və bir-birinə çox bənzər başlıqları çıxarır.
4. Mənbələri növbə ilə gəzərək TARGET_COUNT qədər xəbər seçir.
5. Nəticəni selected_news.json faylına yazır.
"""

import difflib
import html
import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
from bs4 import BeautifulSoup

# ---------------- Ayarlar ----------------
FEEDS = [
    # --- Yoxlanılıb, işləyir ---
    ("AZƏRTAC", "https://azertag.az/rss"),
    ("Oxu.az", "https://oxu.az/feed"),
    ("Publika", "https://publika.az/rss/"),
    ("Day.az", "https://news.day.az/rss/"),
    ("Sia.az", "https://sia.az/feed.php"),
    ("APA", "https://apa.az/rss"),
    ("Trend.az", "https://az.trend.az/rss"),
    ("Qafqazinfo", "https://qafqazinfo.az/rss"),
    ("Xəzər Xəbər", "https://xezerxeber.az/rss"),
    ("Haqqin.az", "https://haqqin.az/rss.xml"),
    ("Metbuat.az", "https://metbuat.az/rss.xml"),
    ("Minval.az", "https://minval.az/feed/"),
    # Baku TV: 17.11-də işləyirdi, 19.11-də HTTP 403 verdi (GitHub serverlərini bəzən bloklayır)
    ("Baku TV", "https://baku.tv/rss"),
    # --- RSS-i olmayan saytlar: xəbər siyahısı səhifəsindən oxunur ("html" rejimi) ---
    # Üçüncü sütun "html", dördüncü sütun xəbərlərin siyahısı olan səhifədir.
    ("AzTV", "https://aztv.az/", "html", "https://aztv.az/az/all-news"),
    ("Baku.ws", "https://baku.ws/", "html", "https://baku.ws/"),
    ("İctimai TV", "https://ictimai.tv/", "html", "https://ictimai.tv/"),
    ("Daxili İşlər Nazirliyi", "https://mia.gov.az/", "html", "https://mia.gov.az/az/news/archive/"),
    ("Fövqəladə Hallar Nazirliyi", "https://fhn.gov.az/", "html", "https://fhn.gov.az/az/melumatlar"),
    ("Azərbaycan Yerüstü Nəqliyyat Agentliyi", "https://ayna.gov.az/", "html", "https://ayna.gov.az/az/news"),
    ("Müdafiə Nazirliyi", "https://mod.gov.az/", "html", "https://mod.gov.az/az/"),
    ("Xarici İşlər Nazirliyi", "https://mfa.gov.az/", "html", "https://mfa.gov.az/az"),
    ("Dövlət Sərhəd Xidməti", "https://dsx.gov.az/", "html", "https://dsx.gov.az/"),
    ("Təhsil Nazirliyi", "https://edu.gov.az/", "html", "https://edu.gov.az/az"),
    ("Səhiyyə Nazirliyi", "https://sehiyye.gov.az/", "html", "https://sehiyye.gov.az/"),
    ("İqtisadiyyat Nazirliyi", "https://economy.gov.az/", "html", "https://economy.gov.az/"),
    ("Prezident.az", "https://president.az/", "html", "https://president.az/az"),
]

TARGET_COUNT = int(os.getenv("TARGET_COUNT", "6"))   # gündə/seansda neçə xəbər
MAX_AGE_HOURS = int(os.getenv("MAX_AGE_HOURS", "24"))  # nə qədər köhnə xəbərlər olar
SIMILARITY = 0.70                                    # başlıqlar bu qədər oxşardırsa, təkrar sayılır
SEEN_FILE = Path("seen.json")
OUT_FILE = Path("selected_news.json")
STATUS_FILE = Path("sources_status.json")
AD_PREFIXES = ("®", "reklam", "pr:", "sponsor")      # ödənişli/reklam xəbərləri atılır
MIN_SNIPPET = 80                                     # bundan qısa xülasə varsa, tam mətn lazım olacaq
USER_AGENT = "Mozilla/5.0 (compatible; ReportBakuBot/1.0)"
# -----------------------------------------


def clean(text):
    """HTML teqlərini və artıq boşluqları təmizləyir."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def entry_time(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    return datetime(*t[:6], tzinfo=timezone.utc)


def is_similar(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= SIMILARITY


def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    # yalnız son 2000 linki saxlayırıq
    SEEN_FILE.write_text(
        json.dumps(sorted(seen)[-2000:], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def discover_feed(page_url):
    """Saytın ana səhifəsindəki <link rel="alternate" type="application/rss+xml"> etiketindən RSS ünvanını tapır."""
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            charset = r.headers.get_content_charset() or "utf-8"
        soup = BeautifulSoup(raw.decode(charset, errors="replace"), "html.parser")
        for link in soup.find_all("link", attrs={"rel": "alternate"}):
            kind = (link.get("type") or "").lower()
            href = link.get("href")
            if href and ("rss" in kind or "atom" in kind):
                return urllib.parse.urljoin(page_url, href)
    except Exception:
        pass
    return None


HTML_MAX_LINKS = 12      # hər saytda siyahıdan ən çox neçə linkə baxılır
HTML_MIN_TITLE = 28      # linkin mətni bundan qısadırsa, xəbər başlığı sayılmır
_robots_cache = {}


def allowed_by_robots(url):
    """Saytın robots.txt faylı bu ünvanı oxumağa icazə verirmi? Fayl oxunmursa, icazə sayılır."""
    parts = urllib.parse.urlsplit(url)
    root = "%s://%s" % (parts.scheme, parts.netloc)
    rp = _robots_cache.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(root + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as r:
                rp.parse(r.read().decode("utf-8", errors="replace").splitlines())
        except Exception:
            rp = False
        _robots_cache[root] = rp
    return True if rp is False else rp.can_fetch(USER_AGENT, url)


def get_page(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "az,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read(2_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
    return BeautifulSoup(raw.decode(charset, errors="replace"), "html.parser")


def _meta(soup, *names):
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) \
              or soup.find("meta", attrs={"itemprop": n})
        if tag and tag.get("content"):
            return clean(tag["content"])
    return ""


def _article_time(soup):
    raw = _meta(soup, "article:published_time", "datePublished", "og:updated_time")
    if not raw:
        t = soup.find("time", attrs={"datetime": True})
        raw = t["datetime"] if t else ""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=4)))   # Bakı vaxtı
    return dt.astimezone(timezone.utc)


def _strip_site_suffix(title):
    # "Xəbər başlığı | AzTV" -> "Xəbər başlığı"
    for sep in (" | ", " - ", " – ", " — "):
        if sep in title:
            head, tail = title.rsplit(sep, 1)
            if len(tail) <= 30 and len(head) >= 20:
                return head.strip()
    return title


def candidate_links(listing_url, soup):
    """Siyahı səhifəsindən xəbər linklərini tapır: eyni sayt, kifayət qədər uzun mətn, yolunda rəqəm var."""
    host = urllib.parse.urlsplit(listing_url).netloc.lower().removeprefix("www.")
    seen_urls, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urllib.parse.urljoin(listing_url, href).split("#")[0]
        parts = urllib.parse.urlsplit(url)
        if parts.netloc.lower().removeprefix("www.") != host:
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|docx?|xlsx?|zip)$", parts.path, re.I):
            continue
        if not re.search(r"\d", parts.path):
            continue
        text = clean(a.get_text(" "))
        if len(text) < HTML_MIN_TITLE or len(text.split()) < 4:
            continue
        if url.rstrip("/") == listing_url.rstrip("/") or url in seen_urls:
            continue
        seen_urls.add(url)
        out.append((url, text))
        if len(out) >= HTML_MAX_LINKS:
            break
    return out


def fetch_html(name, listing_url):
    """RSS-i olmayan saytın xəbər siyahısını oxuyur. Eyni formatda (items, vəziyyət, url) qaytarır."""
    if not allowed_by_robots(listing_url):
        return [], "robots.txt oxumağa icazə vermir (buraxıldı)", listing_url
    try:
        soup = get_page(listing_url)
    except Exception as e:
        return [], "SƏHİFƏ AÇILMADI: %s" % str(e)[:80], listing_url
    links = candidate_links(listing_url, soup)
    if not links:
        return [], "OXUNDU, amma xəbər linki tapılmadı", listing_url

    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    items = []

    def _article(pair):
        url, text = pair
        if not allowed_by_robots(url):
            return None
        try:
            page = get_page(url)
        except Exception:
            return None
        title = _strip_site_suffix(_meta(page, "og:title", "twitter:title") or text)
        when = _article_time(page)
        if when and when < cutoff:
            return None
        desc = _meta(page, "og:description", "description")[:500]
        return {
            "source": name,
            "title": title,
            "snippet": desc,
            "needs_fulltext": len(desc) < MIN_SNIPPET,
            "link": url,
            "published": when.isoformat() if when else None,
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        for it in pool.map(_article, links):
            if it and len(it["title"]) >= 15:
                items.append(it)
    if not items:
        return [], "OXUNDU (%d link), amma təzə xəbər tapılmadı" % len(links), listing_url
    no_date = sum(1 for i in items if not i["published"])
    note = ", tarixsiz: %d" % no_date if no_date else ""
    return items, "OK/HTML (%d xəbər%s)" % (len(items), note), listing_url


def fetch_feed(name, url, auto=False):
    """Bir mənbəni oxuyur. (xəbərlər_siyahısı, vəziyyət_mətni, istifadə_olunan_url) qaytarır."""
    if auto:
        found = discover_feed(url)
        if not found:
            return [], "RSS tapılmadı (səhifə açılmadı və ya RSS linki yoxdur)", url
        url = found
    try:
        feed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        return [], f"XƏTA: {e}", url

    status = getattr(feed, "status", None)
    if not feed.entries:
        return [], f"BOŞ/ÖLÜ (HTTP {status})", url

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    items = []
    for e in feed.entries:
        title = clean(e.get("title"))
        link = e.get("link", "").strip()
        if not title or not link:
            continue
        if title.lower().startswith(AD_PREFIXES):
            continue  # reklam xəbəridir
        when = entry_time(e)
        if when and when < cutoff:
            continue  # çox köhnədir
        snippet = clean(e.get("summary"))[:500]
        items.append(
            {
                "source": name,
                "title": title,
                # qısa parça yalnız növbəti addımda AI-yə "mövzunu anlamaq" üçün veriləcək
                "snippet": snippet,
                "needs_fulltext": len(snippet) < MIN_SNIPPET,
                "link": link,
                "published": when.isoformat() if when else None,
            }
        )
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    return items, f"OK ({len(items)} təzə xəbər, HTTP {status})", url


def main():
    seen = load_seen()
    per_source = {}

    print("=== Mənbələrin yoxlanması ===")
    status = {}
    def _load(entry):
        mode = entry[2] if len(entry) > 2 else ""
        if mode == "html":
            try:
                return fetch_html(entry[0], entry[3] if len(entry) > 3 else entry[1])
            except Exception as e:
                return [], "XƏTA: %s" % str(e)[:80], entry[1]
        return fetch_feed(entry[0], entry[1], auto=(mode == "auto"))

    with ThreadPoolExecutor(max_workers=8) as pool:     # mənbələri paralel yoxlayırıq (daha sürətli)
        results = list(pool.map(_load, FEEDS))

    for entry, (items, state, used_url) in zip(FEEDS, results):
        name = entry[0]
        status[name] = {"url": used_url, "state": state}
        print(f"{name:22s} {state}")
        fresh = [i for i in items if i["link"] not in seen]
        if fresh:
            per_source[name] = fresh

    # Mənbələri növbə ilə gəzirik ki, bir saytın xəbərləri üstünlük təşkil etməsin
    selected = []
    while len(selected) < TARGET_COUNT and any(per_source.values()):
        names = list(per_source)
        random.shuffle(names)  # sıra hər dəfə dəyişir ki, siyahının əvvəlindəki saytlar həmişə üstün olmasın
        for name in names:
            queue = per_source[name]
            while queue:
                cand = queue.pop(0)
                if any(is_similar(cand["title"], s["title"]) for s in selected):
                    continue  # təkrardır
                selected.append(cand)
                break
            if not queue:
                per_source.pop(name, None)
            if len(selected) >= TARGET_COUNT:
                break

    STATUS_FILE.write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    OUT_FILE.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for s in selected:
        seen.add(s["link"])
    save_seen(seen)

    print(f"\n=== Seçilmiş xəbərlər: {len(selected)} ===")
    for i, s in enumerate(selected, 1):
        print(f"{i}. [{s['source']}] {s['title']}\n   {s['link']}")


if __name__ == "__main__":
    main()
