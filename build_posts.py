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
            img = Path(p.get("image", ""))
            if img.exists():
                img.unlink()
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
    parts = [d["headline"], d["summary"], d.get("source_note", ""), HASHTAGS]
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


def main():
    if not IN_FILE.exists():
        print("post_drafts.json yoxdur.")
        raise SystemExit(1)
    drafts = json.loads(IN_FILE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(exist_ok=True)

    pending = cleanup(load_pending())
    baku = datetime.now(timezone.utc) + timedelta(hours=4)
    stamp = baku.strftime("%Y%m%d-%H%M%S")

    for i, d in enumerate(drafts, 1):
        post_id = "%s-%02d" % (stamp, i)
        out = OUT_DIR / (post_id + ".jpg")
        variant = VARIANTS[(len(pending)) % 2]
        photo = pick_photo(d["headline"])
        print("%d/%d %s  [%s, foto: %s]" % (i, len(drafts), d["headline"], variant, photo or "yoxdur"))

        make_post(
            photo, d["category"], d["headline"], d["summary"], str(out),
            variant=variant,
            highlight=d.get("highlight", []),
            source_note=d.get("source_note", ""),
        )

        entry = {
            "id": post_id,
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "category": d["category"],
            "headline": d["headline"],
            "summary": d["summary"],
            "source_note": d.get("source_note", ""),
            "link": d.get("link", ""),
            "sensitive": bool(d.get("sensitive")),
            "image": str(out).replace("\\", "/"),
            "caption": build_caption(d),
        }
        pending.append(entry)

        warn = "⚠️ HƏSSAS MÖVZU: paylaşmazdan əvvəl mənbə ilə yoxla\n\n" if entry["sensitive"] else ""
        tg_caption = "%s%s\n\n%s\n\n%s\n%s" % (
            warn, d["headline"], d["summary"], d.get("source_note", ""), d.get("link", ""))
        send_photo(str(out), tg_caption, post_id)

    PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nTəsdiq gözləyən yeni postlar: %d" % len(drafts))


if __name__ == "__main__":
    main()
