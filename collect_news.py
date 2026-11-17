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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

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


def fetch_feed(name, url):
    """Bir mənbəni oxuyur. (xəbərlər_siyahısı, vəziyyət_mətni) qaytarır."""
    try:
        feed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        return [], f"XƏTA: {e}"

    status = getattr(feed, "status", None)
    if not feed.entries:
        return [], f"BOŞ/ÖLÜ (HTTP {status})"

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
    return items, f"OK ({len(items)} təzə xəbər, HTTP {status})"


def main():
    seen = load_seen()
    per_source = {}

    print("=== Mənbələrin yoxlanması ===")
    status = {}
    for name, url in FEEDS:
        items, state = fetch_feed(name, url)
        status[name] = {"url": url, "state": state}
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
