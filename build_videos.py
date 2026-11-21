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
import subprocess
import time
from pathlib import Path

import requests

from build_video import make_video, VW, VH
from build_posts import pick_photo
import stock_media
from stock_media import find_clip

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


def window_note(p, inset):
    """Redaktor üçün: kiçik pəncərə niyə var/yoxdur."""
    if inset:
        src = inset.get("credit") or "öz kitabxana"
        return "🖼 Pəncərə: %s %s (%s)" % (src, "videosu" if inset["kind"] == "video" else "fotosu",
                                           inset.get("keyword", ""))
    if p.get("sensitive"):
        return "🖼 Pəncərə yoxdur: həssas mövzu"
    if not p.get("visual_keywords"):
        return "🖼 Pəncərə yoxdur: AI görüntü üçün açar söz vermədi"
    if not (stock_media.PIXABAY_KEY or stock_media.PEXELS_KEY):
        return "🖼 Pəncərə yoxdur: PIXABAY_API_KEY işə düşmür (GitHub Secrets və collect.yml-i yoxlayın)"
    return "🖼 Pəncərə yoxdur: stok arxivdə tapılmadı (%s) %s" % (
        ", ".join(p["visual_keywords"]), stock_media.LAST_ERROR)


def make_cover(video_path, cover_path, second=3):
    """Videonun 3-cü saniyəsindən örtük şəkli çıxarır (başlıq və görüntü artıq görünür)."""
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(second), "-i", str(video_path),
                        "-frames:v", "1", "-q:v", "2", str(cover_path)], check=False, timeout=60)
    except Exception:
        return False
    return Path(cover_path).exists() and Path(cover_path).stat().st_size > 5000


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
        inset = None
        if not p.get("sensitive") and p.get("visual_keywords"):
            inset = find_clip(p["visual_keywords"], p["headline"], "stock_tmp", p["id"])
            if not inset:
                own = pick_photo(p["headline"])          # stok tapılmadısa öz kitabxanamızdan foto
                if own:
                    inset = {"path": own, "kind": "photo", "credit": "", "keyword": "öz foto"}
            print("  Pəncərə:", ("%s (%s)" % (inset["kind"], inset["keyword"])) if inset else "yoxdur")
        res = make_video(p["category"], p["headline"], p["summary"], "",      # videoda mənbə yazılmır
                         str(out), photo=pick_photo(p["headline"]),
                         highlight=p.get("highlight", []), voiceover=p.get("voiceover", ""),
                         inset=inset)
        time.sleep(PAUSE_SECONDS)
        if not res:
            p["status"] = "failed"
            print("  Video alınmadı.")
            tg_message("⚠️ Video hazırlanmadı (səs xətası və ya limit):\n%s\n%s" % (
                p["headline"], p.get("link", "")))
            continue
        made += 1

        if inset and res and inset.get("credit"):
            credit = "İllüstrativ görüntü: %s" % inset["credit"]
            cap = p["caption"]
            cut = cap.rfind("\n\n#")
            p["caption"] = (cap[:cut] + "\n\n" + credit + cap[cut:]) if cut > 0 else (cap + "\n\n" + credit)
        cover = OUT_DIR / (p["id"] + ".jpg")
        if make_cover(out, cover):
            p["cover"] = cover.name                     # Reels örtüyü (cover_url)
        p["status"] = "pending"
        p["video"] = out.name
        send_video(str(out),
                   "🎬 REELS ÖNİZLƏMƏ (%d san., %d söz)\n%s\n\n%s\n\n%s\n%s" % (
                       round(res["duration"]), res.get("words", 0), window_note(p, inset), p["headline"],
                       p.get("source_note", ""), p.get("link", "")),
                   p["id"])
        tg_message("📝 Reels-in Instagram mətni (yuxarıdakı videonun altında):\n\n" + p["caption"])

    PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nHazırlanan videolar: %d" % made)


if __name__ == "__main__":
    main()
