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
    # --- Rəsmi qurumların açıq Telegram kanalları ("tg"; kanalın adı t.me/ünvanından götürülür) ---
    # Dövlət saytları (mia.gov.az, fhn.gov.az və s.) GitHub serverlərindən açılmır (20.11.2026 yoxlaması),
    # ona görə onların Telegram kanalları işlədilir. Yeni kanal əlavə etmək üçün bir sətir yazın.
    ("Daxili İşlər Nazirliyi", "polisazerbaycan", "tg"),
]

# Xəbərin ÖZ şəklini götürməyə icazə verilən mənbələr. Müəllif hüququ: başqa portalın şəklini icazəsiz
# paylaşmaq risklidir (Instagram videonu silə, hesaba məhdudiyyət qoya bilər). Yalnız rəsmi qurumların öz
# paylaşdığı mətbuat şəkilləri və ya yazılı icazə aldığınız mənbələr bura əlavə edilməlidir.
IMAGE_OK_SOURCES = {"Daxili İşlər Nazirliyi"}

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


PRACTICAL_WORDS = ["yol", "hava", "qiymət", "tarif", "xidmət", "məktəb", "ödəniş", "qanun", "sağlamlıq",
                   "nəqliyyat", "metro", "rüsum", "əmək haqqı", "bağlan", "istifadə", "sakin", "kommunal", "təhsil"]


def practical_score(title):
    t = title.lower()
    return sum(1 for w in PRACTICAL_WORDS if w in t)


def _stems(text):
    """Mənalı sözlərin ilk 4 hərfi (Azərbaycan dilində şəkilçilər uzun olur, kökü müqayisə edirik)."""
    words = re.findall(r"[\w]+", text.lower())
    return {w[:4] for w in words if len(w) >= 4}


def is_similar(a, b):
    """İki başlıq eyni hadisədən bəhs edirsə True: hərf oxşarlığı və ya ortaq söz köklərinin çoxluğu."""
    if difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= SIMILARITY:
        return True
    sa, sb = _stems(a), _stems(b)
    common = sa & sb
    return len(common) >= 3 and len(common) / float(min(len(sa), len(sb)) or 1) >= 0.4


def recent_titles(hours=72):
    """Son 72 saatda hazırlanmış xəbərlərin başlıqları (AI başlığı və mənbə başlığı)."""
    f = Path("pending.json")
    if not f.exists():
        return []
    try:
        items = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for p in items:
        try:
            if now - datetime.fromisoformat(p["created"]) <= timedelta(hours=hours):
                out += [t for t in (p.get("headline"), p.get("source_title")) if t]
        except Exception:
            continue
    return out


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


AZ_MONTHS = {"yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6, "iyul": 7,
             "avqust": 8, "sentyabr": 9, "oktyabr": 10, "noyabr": 11, "dekabr": 12}
DATE_NUM = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
DATE_TXT = re.compile(r"(\d{1,2})\s+(" + "|".join(AZ_MONTHS) + r"),?\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?",
                      re.IGNORECASE)


def parse_az_date(text):
    """'12.11.2026' və ya '19 Noyabr, 2026 15:38' -> UTC datetime (Bakı vaxtı UTC+4). Tapılmasa None."""
    m = DATE_TXT.search(text)
    try:
        if m:
            day, month, year = int(m.group(1)), AZ_MONTHS[m.group(2).lower()], int(m.group(3))
            hour, minute = (int(m.group(4)), int(m.group(5))) if m.group(4) else (12, 0)
        else:
            m = DATE_NUM.search(text)
            if not m:
                return None
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour, minute = 12, 0
        local = datetime(year, month, day, hour, minute)
    except (ValueError, KeyError):
        return None
    return (local - timedelta(hours=4)).replace(tzinfo=timezone.utc)


def fetch_html_news(name, url, pattern):
    """RSS-i olmayan saytın xəbər səhifəsini oxuyur. Yalnız tarixi aydın olan xəbərləri götürür."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            charset = r.headers.get_content_charset() or "utf-8"
    except Exception as e:
        return [], "SƏHİFƏ AÇILMADI (%s)" % type(e).__name__, url
    soup = BeautifulSoup(raw.decode(charset, errors="replace"), "html.parser")
    rx = re.compile(pattern)
    links = [a for a in soup.find_all("a", href=True) if rx.search(a["href"])]
    if not links:
        return [], "0 xəbər linki tapıldı (qayda uyğun deyil və ya səhifə JavaScript ilə yüklənir)", url

    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    items, seen_links = [], set()
    links.sort(key=lambda a: 0 if len(clean(a.get_text(" "))) >= 15 else 1)   # mətnli linklər əvvəl (başlıq üçün)
    for a in links:
        link = urllib.parse.urljoin(url, a["href"]).split("#")[0]
        if link in seen_links:
            continue
        text = clean(a.get_text(" "))
        text = clean(text.replace("event.title", ""))
        # tarixi eyni xəbər qutusunda axtarırıq (qutuda yalnız 1 xəbər linki olmalıdır)
        when, box_text = None, text
        node = a
        for _ in range(4):
            node = node.parent
            if node is None:
                break
            inside = {urllib.parse.urljoin(url, x["href"]).split("#")[0] for x in node.find_all("a", href=True)
                      if rx.search(x["href"])}
            if len(inside) > 1:
                break
            box_text = clean(node.get_text(" "))
            when = parse_az_date(box_text) or parse_az_date(text)
            if when:
                break
        if when is None:
            when = parse_az_date(text)
        if when is None or when < cutoff:
            continue                       # tarixi bəlli olmayan və ya köhnə xəbəri götürmürük
        seen_links.add(link)
        title = DATE_TXT.sub("", DATE_NUM.sub("", text)).strip(" -:|")
        if len(title) < 15:
            title = clean(DATE_TXT.sub("", DATE_NUM.sub("", box_text))).strip(" -:|")
        if len(title) < 15:
            continue
        items.append({
            "source": name,
            "title": title[:160],
            "snippet": clean(DATE_TXT.sub("", DATE_NUM.sub("", box_text)))[:500],
            "needs_fulltext": True,
            "link": link,
            "published": when.isoformat(),
        })
    items.sort(key=lambda x: x["published"], reverse=True)
    return items, "OK (%d təzə xəbər, səhifə oxundu)" % len(items), url


def fetch_telegram_channel(name, handle):
    """Açıq Telegram kanalının (t.me/s/<kanal>) son yazılarını oxuyur."""
    url = "https://t.me/s/" + handle
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
    except Exception as e:
        return [], "KANAL AÇILMADI (%s)" % type(e).__name__, url
    soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
    posts = soup.select("div.tgme_widget_message[data-post]")
    if not posts:
        return [], "yazı tapılmadı (kanal açıq deyil və ya adı səhvdir)", url
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    items = []
    for m in posts:
        text_el = m.select_one(".tgme_widget_message_text")
        time_el = m.select_one("time[datetime]")
        text = clean(text_el.get_text(" ")) if text_el else ""
        if len(text) < 60 or not time_el:
            continue
        try:
            when = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < cutoff:
            continue
        title = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0][:160]
        image_url = None
        if name in IMAGE_OK_SOURCES:
            ph = m.select_one("a.tgme_widget_message_photo_wrap")
            mt = re.search(r"url\(['\"]?([^'\")]+)", (ph.get("style") or "")) if ph else None
            image_url = mt.group(1) if mt else None
        items.append({
            "image_url": image_url,
            "source": name,
            "title": title,
            "snippet": text[:1500],
            "needs_fulltext": False,
            "link": "https://t.me/" + m["data-post"],
            "published": when.isoformat(),
        })
    items.sort(key=lambda x: x["published"], reverse=True)
    return items, "OK (%d təzə xəbər, Telegram kanalı)" % len(items), url


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
        if len(entry) > 3 and entry[2] == "html":
            return fetch_html_news(entry[0], entry[1], entry[3])
        if len(entry) > 2 and entry[2] == "tg":
            return fetch_telegram_channel(entry[0], entry[1])
        return fetch_feed(entry[0], entry[1], auto=len(entry) > 2 and entry[2] == "auto")

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
    for name in per_source:                       # 4: praktik faydalı mövzulara üstünlük
        per_source[name].sort(key=lambda x: -practical_score(x["title"]))
    selected = []
    earlier = recent_titles()
    while len(selected) < TARGET_COUNT and any(per_source.values()):
        names = list(per_source)
        random.shuffle(names)  # sıra hər dəfə dəyişir ki, siyahının əvvəlindəki saytlar həmişə üstün olmasın
        for name in names:
            queue = per_source[name]
            while queue:
                cand = queue.pop(0)
                if any(is_similar(cand["title"], s["title"]) for s in selected) or \
                        any(is_similar(cand["title"], t) for t in earlier):
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
