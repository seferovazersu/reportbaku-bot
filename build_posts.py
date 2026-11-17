"""
ReportBaku - 3-cü addım: post_drafts.json -> Instagram şəkilləri -> Telegram-a baxış üçün.

Hər xəbər üçün posts/ qovluğunda JPEG şəkil yaradılır (iki şablon növbələşir),
sonra şəkil başlıq/xülasə ilə birlikdə Telegram-a göndərilir.
Foto: photos/ qovluğunda şəkil varsa, onlardan seçilir; yoxdursa markalı Bakı fonu işlənir.
"""

import hashlib
import json
import os
from pathlib import Path

import requests

from make_post import make_post

IN_FILE = Path("post_drafts.json")
OUT_DIR = Path("posts")
PHOTO_DIR = Path("photos")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
VARIANTS = ["overlay", "panel"]


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


def send_photo(path, caption):
    if not (TG_TOKEN and TG_CHAT):
        print("  Telegram ayarları yoxdur, göndərilmir.")
        return
    try:
        with open(path, "rb") as f:
            r = requests.post(
                "https://api.telegram.org/bot%s/sendPhoto" % TG_TOKEN,
                data={"chat_id": TG_CHAT, "caption": caption[:1000]},
                files={"photo": f},
                timeout=60,
            )
        if not r.ok:
            print("  Telegram xətası:", r.status_code, r.text[:200])
    except Exception as e:
        print("  Telegram xətası:", str(e)[:150])


def main():
    if not IN_FILE.exists():
        print("post_drafts.json yoxdur.")
        raise SystemExit(1)
    drafts = json.loads(IN_FILE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(exist_ok=True)

    for i, d in enumerate(drafts, 1):
        out = OUT_DIR / ("post_%02d.jpg" % i)
        photo = pick_photo(d["headline"])
        print("%d/%d %s  [%s, foto: %s]" % (i, len(drafts), d["headline"],
              VARIANTS[(i - 1) % 2], photo or "yoxdur"))
        make_post(
            photo, d["category"], d["headline"], d["summary"], str(out),
            variant=VARIANTS[(i - 1) % 2],
            highlight=d.get("highlight", []),
            source_note=d.get("source_note", ""),
        )
        warn = "⚠️ HƏSSAS MÖVZU: paylaşmazdan əvvəl mənbə ilə yoxla\n\n" if d.get("sensitive") else ""
        caption = "%s%s\n\n%s\n\n%s\n%s" % (
            warn, d["headline"], d["summary"], d.get("source_note", ""), d.get("link", ""))
        send_photo(str(out), caption)

    print("\nŞəkillər hazırdır: %d" % len(drafts))


if __name__ == "__main__":
    main()
