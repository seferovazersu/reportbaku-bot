"""
ReportBaku - Reels addımı: növbədəki (queued) Reels qeydləri üçün səsli video hazırlayır.

pending.json-da status "queued" olan Reels qeydləri üçün:
  - videos_out/<id>.mp4 videosu yaradılır (Gemini səsi + altyazılı animasiya),
  - qeyd "pending" olur (təsdiq gözləyir),
  - video Telegram-a [🎬 Reels paylaş] [❌ Ləğv et] düymələri ilə, ardınca Instagram mətni göndərilir.
Videonu media budağına göndərməyi iş axını (workflow) edir, paylaşımı poll_approvals.py.
"""

import json
import os
import time
from pathlib import Path

import requests

from build_video import make_video, VW, VH
from build_posts import pick_photo

PENDING_FILE = Path("pending.json")
OUT_DIR = Path("videos_out")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
MAX_VIDEOS = int(os.environ.get("MAX_VIDEOS", "3"))          # bir işə düşəndə ən çox neçə video
PAUSE_SECONDS = 6


def tg_message(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                      data={"chat_id": TG_CHAT, "text": text[:4000], "disable_web_page_preview": "true"},
                      timeout=60)
    except Exception as e:
        print("  Telegram xətası:", str(e)[:150])


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
    made = 0

    for p in pending:
        if p.get("kind") != "reel" or p.get("status") != "queued":
            continue
        if made >= MAX_VIDEOS:
            print("Video limiti dolub, qalanı növbədə qalır: %s" % p["headline"])
            continue

        print("Video: %s" % p["headline"])
        out = OUT_DIR / (p["id"] + ".mp4")
        res = make_video(p["category"], p["headline"], p["summary"], p.get("source_note", ""),
                         str(out), photo=pick_photo(p["headline"]),
                         highlight=p.get("highlight", []), voiceover=p.get("voiceover", ""))
        time.sleep(PAUSE_SECONDS)
        if not res:
            p["status"] = "failed"
            print("  Video alınmadı.")
            tg_message("⚠️ Video hazırlanmadı (səs xətası və ya limit):\n%s\n%s" % (
                p["headline"], p.get("link", "")))
            continue
        made += 1

        p["status"] = "pending"
        p["video"] = out.name
        send_video(str(out),
                   "🎬 REELS ÖNİZLƏMƏ (%d san., %d söz)\n\n%s\n\n%s\n%s" % (
                       round(res["duration"]), res.get("words", 0), p["headline"],
                       p.get("source_note", ""), p.get("link", "")),
                   p["id"])
        tg_message("📝 Reels-in Instagram mətni (yuxarıdakı videonun altında):\n\n" + p["caption"])

    PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nHazırlanan videolar: %d" % made)


if __name__ == "__main__":
    main()
