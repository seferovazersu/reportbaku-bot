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
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
from bs4 import BeautifulSoup

# ---------------- Ayarlar ----------------
FEEDS = [
    ("AZƏRTAC", "https://azertag.az/rss"),
    ("Oxu.az", "https://oxu.az/feed"),
    ("Publika", "https://publika.az/rss/"),
    ("Day.az", "https://news.day.az/rss/"),
    ("Sia.az", "https://sia.az/feed.php"),
    ("Azadlıq", "https://azadliq.info/feed/"),
    # Yoxlanılıb, işləyir (17.11.2026). Report.az (HTTP 403) və İctimai TV (cavab yoxdur) çıxarıldı.
    ("APA", "https://apa.az/rss"),
    ("Trend.az", "https://az.trend.az/rss"),
    ("Qafqazinfo", "https://qafqazinfo.az/rss"),
    ("Baku TV", "https://baku.tv/rss"),
    ("Xəzər Xəbər", "https://xezerxeber.az/rss"),
    # --- Yeni namizədlər: ana səhifədən RSS linkini özü tapır ("auto"). Nəticəni sources_status.json-da görəcəksiniz.
    ("Milli.az", "https://milli.az/", "auto"),
    ("Haqqin.az", "https://haqqin.az/", "auto"),
    ("Musavat.com", "https://musavat.com/", "auto"),
    ("1news.az", "https://1news.az/", "auto"),
    ("Modern.az", "https://modern.az/", "auto"),
    ("Metbuat.az", "https://metbuat.az/", "auto"),
    ("Interfax.az", "https://interfax.az/", "auto"),
    ("Minval.az", "https://minval.az/", "auto"),
    ("BBC Azərbaycanca", "https://www.bbc.com/azeri", "auto"),
    ("Azadlıq Radiosu", "https://www.azadliq.org/", "auto"),
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
        with urllib.request.urlopen(req, timeout=25) as r:
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
    for entry in FEEDS:
        name, url = entry[0], entry[1]
        items, state, used_url = fetch_feed(name, url, auto=len(entry) > 2 and entry[2] == "auto")
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
