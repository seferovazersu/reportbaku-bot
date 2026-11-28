"""
ReportBaku - 3-cü addım: post_drafts.json -> şəkillər -> Telegram-a təsdiq üçün (düymələrlə).

Hər xəbər üçün:
  - posts/<id>.jpg şəkli yaradılır (iki şablon növbələşir),
  - pending.json-a "gözləyir" kimi yazılır,
  - Telegram-a şəkil + [Paylaş] [Ləğv et] düymələri göndərilir.
Paylaşımı poll_approvals.py edir (düyməyə basılandan sonra).
"""

import hashlib
import json
import re
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from make_post import make_post

IN_FILE = Path("post_drafts.json")
PENDING_FILE = Path("pending.json")
OUT_DIR = Path("posts")
PHOTO_DIR = Path("photos")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
VARIANTS = ["overlay", "panel"]
HASHTAGS = os.getenv("HASHTAGS", "#ReportBaku #Azərbaycan #Bakı #xəbər #sonxəbər")
REELS_PER_RUN = int(os.getenv("REELS_PER_RUN", "2"))   # hər işə salmada neçə Reels
POSTS_PER_RUN = int(os.getenv("POSTS_PER_RUN", "1"))   # hər işə salmada neçə foto post
KEEP_DAYS_IMAGES = 3     # köhnə şəkillər bu qədər gündən sonra silinir
KEEP_DAYS_RECORDS = 7    # qeydlər (təkrar yoxlaması üçün) bu qədər saxlanır


def load_pending():
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def cleanup(pending):
    now = datetime.now(timezone.utc)
    keep = []
    for p in pending:
        try:
            age = now - datetime.fromisoformat(p["created"])
        except Exception:
            age = timedelta(0)
        if age > timedelta(days=KEEP_DAYS_IMAGES):
            image = p.get("image")
            if image and Path(image).is_file():
                Path(image).unlink()
        if age <= timedelta(days=KEEP_DAYS_RECORDS):
            keep.append(p)
    return keep


def pick_photo(headline):
    """photos/ qovluğundakı fotolardan birini seçir (eyni başlıq həmişə eyni foto)."""
    if not PHOTO_DIR.exists():
        return None
    files = sorted(
        p for p in PHOTO_DIR.rglob("*")
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    )
    if not files:
        return None
    n = int(hashlib.md5(headline.encode("utf-8")).hexdigest(), 16)
    return str(files[n % len(files)])


CATEGORY_TAGS = {"SİYASƏT": "#siyasət", "İQTİSADİYYAT": "#iqtisadiyyat", "CƏMİYYƏT": "#cəmiyyət",
                 "HADİSƏ": "#hadisə", "DÜNYA": "#dünya", "İDMAN": "#idman", "MƏDƏNİYYƏT": "#mədəniyyət",
                 "TEXNOLOGİYA": "#texnologiya", "SAĞLAMLIQ": "#sağlamlıq"}
MAX_TAGS = 10


def build_hashtags(d):
    """Sabit əsas heştaqlar + kateqoriya heştaqı + xəbərə aid AI heştaqları (təkrarsız, ən çox 10)."""
    tags = HASHTAGS.split() + [CATEGORY_TAGS.get(d.get("category", ""), "")] + list(d.get("hashtags") or [])
    out, seen = [], set()
    for t in tags:
        t = t.strip()
        if not t.startswith("#") or len(t) < 3 or " " in t:
            continue
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return " ".join(out[:MAX_TAGS])


SRC_NAMES = ["AZƏRTAC", "AZERTAC", "APA", "Trend", "Report", "Day.az", "Oxu.az", "Oxu", "Publika", "Sia.az", "Sia",
             "Qafqazinfo", "Xəzər Xəbər", "Haqqin.az", "Metbuat.az", "Minval.az", "Baku TV", "Baku.ws", "AzTV",
             "İctimai TV", "Musavat", "Milli.az", "1news", "Modern.az", "Interfax", "BBC", "Azadlıq", "Sputnik",
             "Reuters", "Xinhua"]
SRC_RX = re.compile(r"(?<![\w.])(" + "|".join(re.escape(x) for x in sorted(SRC_NAMES, key=len, reverse=True)) + r")(?![\w.])",
                    re.IGNORECASE)
SRC_PHRASE_RX = re.compile(r"\b(məlumatına görə|xəbər verir|xəbər verib|məlumat verir|yazır ki)\b", re.IGNORECASE)


def scrub_src(text):
    """Mənbə adı və ya mənbə ifadəsi olan cümləni çıxarır (Instagram mətni üçün)."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    kept = [s for s in parts if s.strip() and not SRC_RX.search(s) and not SRC_PHRASE_RX.search(s)]
    if kept:
        return " ".join(kept).strip()
    cleaned = SRC_PHRASE_RX.sub("", SRC_RX.sub("", text or ""))
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,.-:")


CTAS = ["Bu sizə necə təsir edəcək? Şərhdə yazın.",
        "Dostunuza göndərin, bilməlidir.",
        "Fikrinizi şərhdə bildirin.",
        "Bu xəbəri yadda saxlayın."]


def build_caption(d, credit=""):
    """Instagram-da görünəcək mətn: başlıq, ətraflı yazı, mənbə, foto krediti, heştaqlar."""
    body = d.get("caption_text") or d["summary"]
    chan = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    tg_line = ("📲 Ətraflı və tam xəbər: t.me/%s" % chan.lstrip("@")) if chan.startswith("@") else ""
    parts = [scrub_src(d["headline"]), scrub_src(body),
             "📍 Bakı", tg_line,
             ("Foto: " + credit) if credit else "", build_hashtags(d)]
    return "\n\n".join(p for p in parts if p)


def fetch_source_image(d, base_id):
    """Xəbərin öz şəklini yükləyir (yalnız collect_news.py-də icazəli mənbələr üçün image_url verilir).
    (fayl_yolu, kredit_adı) qaytarır; alınmasa (None, "")."""
    url = d.get("image_url")
    if not url:
        return None, ""
    try:
        r = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0 (compatible; ReportBakuBot/1.0)"})
        if not r.ok or len(r.content) > 12 * 1024 * 1024:
            return None, ""
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(r.content))
        im.load()
        if min(im.size) < 400:
            return None, ""
        Path("src_images").mkdir(exist_ok=True)
        path = Path("src_images") / (base_id + ".jpg")
        im.convert("RGB").save(path, quality=92)
        credit = (d.get("source_note") or "").replace("Mənbə:", "").strip()
        return str(path), credit
    except Exception as e:
        print("  Xəbər şəkli yüklənmədi:", str(e)[:100])
        return None, ""


def send_photo(path, caption, post_id):
    if not (TG_TOKEN and TG_CHAT):
        print("  Telegram ayarları yoxdur, göndərilmir.")
        return False
    markup = {"inline_keyboard": [[
        {"text": "✅ Paylaş", "callback_data": "pub:" + post_id},
        {"text": "❌ Ləğv et", "callback_data": "del:" + post_id},
    ]]}
    try:
        with open(path, "rb") as f:
            r = requests.post(
                "https://api.telegram.org/bot%s/sendPhoto" % TG_TOKEN,
                data={"chat_id": TG_CHAT, "caption": caption[:900],
                      "reply_markup": json.dumps(markup)},
                files={"photo": f},
                timeout=60,
            )
        if not r.ok:
            print("  Telegram xətası:", r.status_code, r.text[:200])
        return r.ok
    except Exception as e:
        print("  Telegram xətası:", str(e)[:150])
        return False


def assign_formats(drafts):
    """Xəbərləri formata bölür: həssas olmayanlar Reels üçün, qalanlar foto post üçün.
    [(format, draft), ...] qaytarır."""
    reels = [d for d in drafts if not d.get("sensitive")][:REELS_PER_RUN]
    rest = [d for d in drafts if not any(d is r for r in reels)]
    posts = rest[:POSTS_PER_RUN]
    skipped = len(drafts) - len(reels) - len(posts)
    if skipped > 0:
        print("Artıq xəbərlər buraxıldı: %d" % skipped)
    return [("reel", d) for d in reels] + [("post", d) for d in posts]


def send_text(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
            data={"chat_id": TG_CHAT, "text": text[:4000], "disable_web_page_preview": "true"},
            timeout=60,
        )
    except Exception as e:
        print("  Telegram xətası:", str(e)[:150])


def main():
    if not IN_FILE.exists():
        print("post_drafts.json yoxdur.")
        raise SystemExit(1)
    drafts = json.loads(IN_FILE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(exist_ok=True)

    pending = cleanup(load_pending())
    baku = datetime.now(timezone.utc) + timedelta(hours=4)
    stamp = baku.strftime("%Y%m%d-%H%M%S")
    plan = assign_formats(drafts)
    n_posts = n_reels = 0

    for i, (fmt, d) in enumerate(plan, 1):
        base_id = "%s-%02d" % (stamp, i)
        photo_file, credit = fetch_source_image(d, base_id)
        caption = build_caption(d, credit)

        if fmt == "reel":
            # video sonrakı addımda (build_videos.py) hazırlanır
            print("%d/%d REELS növbəsinə qoyuldu: %s" % (i, len(plan), d["headline"]))
            pending.append({
                "id": base_id + "-r",
                "kind": "reel",
                "created": datetime.now(timezone.utc).isoformat(),
                "status": "queued",
                "category": d["category"],
                "headline": d["headline"],
                "summary": d["summary"],
                "voiceover": d.get("voiceover", ""),
                "visual_keywords": d.get("visual_keywords", []),
                "highlight": d.get("highlight", []),
                "source_note": d.get("source_note", ""),
                "link": d.get("link", ""),
                "sensitive": False,
                "video": "",
                "photo_file": photo_file,
                "image_credit": credit,
                "caption": caption,
            })
            n_reels += 1
            continue

        out = OUT_DIR / (base_id + ".jpg")
        variant = VARIANTS[(len(pending)) % 2]
        photo = photo_file or pick_photo(d["headline"])
        print("%d/%d FOTO POST: %s  [%s, foto: %s]" % (i, len(plan), d["headline"], variant, photo or "yoxdur"))
        make_post(
            photo, d["category"], d["headline"], d["summary"], str(out),
            variant=variant,
            highlight=d.get("highlight", []),
            source_note="",            # şəkildə mənbə yazılmır, yalnız Instagram mətnində olur
        )
        entry = {
            "id": base_id,
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "category": d["category"],
            "headline": d["headline"],
            "summary": d["summary"],
            "source_note": d.get("source_note", ""),
            "link": d.get("link", ""),
            "sensitive": bool(d.get("sensitive")),
            "highlight": d.get("highlight", []),
            "image": str(out).replace("\\", "/"),
            "caption": caption,
        }
        pending.append(entry)
        n_posts += 1

        warn = "⚠️ HƏSSAS MÖVZU: paylaşmazdan əvvəl mənbə ilə yoxla\n\n" if entry["sensitive"] else ""
        tg_caption = "%s%s\n\n%s\n%s" % (
            warn, d["headline"], d.get("source_note", ""), d.get("link", ""))
        send_photo(str(out), tg_caption, base_id)
        send_text("📝 Instagram-da görünəcək mətn (yuxarıdakı şəklin altında):\n\n" + caption)

    PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nFoto post: %d, Reels növbəsi: %d" % (n_posts, n_reels))


if __name__ == "__main__":
    main()
