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
HASHTAGS = os.getenv("HASHTAGS", "#ReportBaku #xəbər #Azərbaycan #Bakı")
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


def build_caption(d):
    """Instagram-da görünəcək mətn."""
    body = d.get("caption_text") or d["summary"]
    parts = [d["headline"], body, d.get("source_note", ""), HASHTAGS]
    return "\n\n".join(p for p in parts if p)


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
        caption = build_caption(d)

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
                "caption": caption,
            })
            n_reels += 1
            continue

        out = OUT_DIR / (base_id + ".jpg")
        variant = VARIANTS[(len(pending)) % 2]
        photo = pick_photo(d["headline"])
        print("%d/%d FOTO POST: %s  [%s, foto: %s]" % (i, len(plan), d["headline"], variant, photo or "yoxdur"))
        make_post(
            photo, d["category"], d["headline"], d["summary"], str(out),
            variant=variant,
            highlight=d.get("highlight", []),
            source_note=d.get("source_note", ""),
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
