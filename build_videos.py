"""
ReportBaku - Reels addımı: yeni postlar üçün səsli video hazırlayır.

pending.json-dakı yeni (hələ yoxlanmamış) postlar üçün:
  - videos_out/<id>-r.mp4 videosu yaradılır (Gemini səsi + animasiyalı mətn),
  - pending.json-a "reel" qeydi əlavə olunur,
  - video Telegram-a [🎬 Reels paylaş] [❌ Ləğv et] düymələri ilə göndərilir.
Videonu media budağına göndərməyi iş axını (workflow) edir, paylaşımı poll_approvals.py.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from build_video import make_video, VW, VH
from build_posts import pick_photo

PENDING_FILE = Path("pending.json")
OUT_DIR = Path("videos_out")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
MAX_VIDEOS = int(os.environ.get("MAX_VIDEOS", "3"))          # bir işə düşəndə ən çox neçə video
SKIP_SENSITIVE = os.environ.get("VIDEO_SKIP_SENSITIVE", "1") == "1"
PAUSE_SECONDS = 6


def send_video(path, caption, reel_id):
    if not (TG_TOKEN and TG_CHAT):
        print("  Telegram ayarları yoxdur, göndərilmir.")
        return False
    markup = {"inline_keyboard": [[
        {"text": "🎬 Reels paylaş", "callback_data": "reel:" + reel_id},
        {"text": "❌ Ləğv et", "callback_data": "del:" + reel_id},
    ]]}
    try:
        with open(path, "rb") as f:
            r = requests.post(
                "https://api.telegram.org/bot%s/sendVideo" % TG_TOKEN,
                data={"chat_id": TG_CHAT, "caption": caption[:900], "supports_streaming": "true",
                      "width": VW, "height": VH, "reply_markup": json.dumps(markup)},
                files={"video": f}, timeout=180)
        if not r.ok:
            print("  Telegram xətası:", r.status_code, r.text[:200])
        return r.ok
    except Exception as e:
        print("  Telegram xətası:", str(e)[:150])
        return False


def main():
    if not PENDING_FILE.exists():
        print("pending.json yoxdur.")
        return
    pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(exist_ok=True)
    existing = {p["id"] for p in pending}
    made = 0

    for p in list(pending):
        if p.get("kind", "post") != "post" or p.get("video_checked"):
            continue
        p["video_checked"] = True            # eyni xəbər üçün ikinci dəfə cəhd etmirik
        reel_id = p["id"] + "-r"
        if reel_id in existing:
            continue
        if p.get("status") != "pending":
            continue
        if p.get("sensitive") and SKIP_SENSITIVE:
            print("Həssas mövzu, video hazırlanmır: %s" % p["headline"])
            continue
        if made >= MAX_VIDEOS:
            print("Video limiti dolub, qalanı buraxılır: %s" % p["headline"])
            continue

        print("Video: %s" % p["headline"])
        out = OUT_DIR / (reel_id + ".mp4")
        res = make_video(p["category"], p["headline"], p["summary"], p.get("source_note", ""),
                         str(out), photo=pick_photo(p["headline"]),
                         highlight=p.get("highlight", []))
        time.sleep(PAUSE_SECONDS)
        if not res:
            print("  Video alınmadı.")
            continue
        made += 1

        reel = {
            "id": reel_id,
            "kind": "reel",
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "category": p["category"],
            "headline": p["headline"],
            "summary": p["summary"],
            "source_note": p.get("source_note", ""),
            "link": p.get("link", ""),
            "sensitive": False,
            "video": out.name,
            "caption": p["caption"],
        }
        pending.append(reel)
        existing.add(reel_id)
        send_video(str(out),
                   "🎬 REELS ÖNİZLƏMƏ (%d san.)\n\n%s\n\n%s\n%s" % (
                       round(res["duration"]), p["headline"], p.get("source_note", ""), p.get("link", "")),
                   reel_id)

    PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nHazırlanan videolar: %d" % made)


if __name__ == "__main__":
    main()
