"""
ReportBaku - yeni axın, 3-cü addım: "✅ Hazırla" basılan xəbər üçün çıxışları hazırlayır.

Sizin göndərdiyiniz şəkil/video əsasında (və ya mediasız):
  - şəkil postu (və ya şəkil + video olduqda KARUSEL: sağa sürüşdürməklə),
  - Reels (səsli video; pəncərədə sizin şəkil/videonuz; örtük = şəkil + başlıq),
və hamısını Telegram-a təsdiq üçün göndərir (düymələrlə). Öz xəbəriniz (mediaya mətn yazmısınızsa)
üçün mətnləri Gemini sizin yazdığınız mətndən hazırlayır.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from make_post import make_post
from build_video import make_video, make_cover_image, VW, VH
from build_posts import build_caption, cleanup

PENDING_FILE = Path("pending.json")
POSTS_DIR = Path("posts")
VIDEOS_DIR = Path("videos_out")
INCOMING = Path("incoming")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
VARIANTS = ["overlay", "panel"]


# ---------------- Telegram ----------------
def tg_post(method, data, files=None):
    if not (TG_TOKEN and TG_CHAT):
        print("  Telegram ayarları yoxdur.")
        return False
    try:
        r = requests.post("https://api.telegram.org/bot%s/%s" % (TG_TOKEN, method),
                          data=dict(data, chat_id=TG_CHAT), files=files, timeout=180)
        if not r.ok:
            print("  Telegram xətası:", r.status_code, r.text[:200])
        return r.ok
    except Exception as e:
        print("  Telegram xətası:", str(e)[:150])
        return False


def buttons(ok_text, ok_data, id_):
    return json.dumps({"inline_keyboard": [[
        {"text": ok_text, "callback_data": ok_data + ":" + id_},
        {"text": "❌ Ləğv et", "callback_data": "del:" + id_}]]})


def notify(text):
    tg_post("sendMessage", {"text": text[:4000], "disable_web_page_preview": "true"})


# ---------------- Köməkçilər ----------------
def run_ffmpeg(args):
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args, capture_output=True, text=True, timeout=600)
    return r.returncode == 0


def extract_frame(video, out, second=1):
    return run_ffmpeg(["-ss", str(second), "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)]) \
        and Path(out).exists()


def transcode_carousel(src, out):
    """Karusel üçün video: 4:5 (1080x1350), H.264/AAC, ≤58 san."""
    return run_ffmpeg(["-i", str(src), "-t", "58",
                       "-vf", "scale=1080:1350:force_original_aspect_ratio=increase,crop=1080:1350,format=yuv420p",
                       "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                       "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])


def fill_from_text(e):
    """Özünüzün göndərdiyi mətndən başlıq, xülasə, caption, səsləndirmə mətni hazırlayır (Gemini)."""
    from process_news import ask_gemini
    item = {"source": "ReportBaku (öz məlumatımız)", "title": e["raw_text"].split("\n")[0][:120], "link": ""}
    res = ask_gemini(item, e["raw_text"], [])
    if not res or res.get("skip"):
        return False
    e["headline"] = str(res.get("headline", "")).strip().rstrip(".") or e["headline"]
    e["summary"] = str(res.get("summary", "")).strip()
    e["caption_text"] = str(res.get("caption", "")).strip()[:1500] or e["summary"]
    e["voiceover"] = str(res.get("voiceover", "")).strip()[:1100]
    e["category"] = str(res.get("category", "CƏMİYYƏT")).strip().upper() or "CƏMİYYƏT"
    e["hashtags"] = [h for h in (res.get("hashtags") or []) if isinstance(h, str) and h.startswith("#")][:5]
    e["highlight"] = [w for w in (res.get("highlight") or []) if isinstance(w, str) and w in e["headline"]][:3]
    e["sensitive"] = bool(res.get("sensitive"))
    return bool(e["headline"] and e["summary"])


def build_for(e, pending):
    if e.get("custom") and not e.get("summary"):
        if not fill_from_text(e):
            raise RuntimeError("Mətndən xəbər hazırlanmadı (AI xülasə yaza bilmədi)")

    photos = [m["file"] for m in e.get("media", []) if m["type"] == "photo" and Path(m["file"]).exists()]
    videos = [m["file"] for m in e.get("media", []) if m["type"] == "video" and Path(m["file"]).exists()]
    photo = photos[0] if photos else None
    video = videos[0] if videos else None
    if video and not photo:
        frame = INCOMING / e["id"] / "frame.jpg"
        photo = str(frame) if extract_frame(video, frame) else None

    base = e["id"]
    POSTS_DIR.mkdir(exist_ok=True)
    VIDEOS_DIR.mkdir(exist_ok=True)
    caption = build_caption(e)
    variant = VARIANTS[len(pending) % 2]
    both = bool(photos and video)

    # --- 1) şəkil postu (və ya karusel) ---
    img_path = POSTS_DIR / (base + "-p.jpg")
    make_post(photo, e["category"], e["headline"], e["summary"], str(img_path), variant=variant,
              highlight=e.get("highlight", []), source_note="")
    entry_common = {"created": datetime.now(timezone.utc).isoformat(), "status": "pending",
                    "category": e["category"], "headline": e["headline"], "summary": e["summary"],
                    "source_note": e.get("source_note", ""), "link": e.get("link", ""),
                    "sensitive": e["sensitive"], "caption": caption}
    warn = "⚠️ HƏSSAS MÖVZU: paylaşmazdan əvvəl yoxlayın\n\n" if e["sensitive"] else ""
    if both and transcode_carousel(video, VIDEOS_DIR / (base + "-k.mp4")):
        kid = base + "-k"
        pending.append(dict(entry_common, id=kid, kind="carousel", images=[str(img_path).replace("\\", "/")],
                            video=kid + ".mp4"))
        with open(img_path, "rb") as f:
            tg_post("sendPhoto", {"caption": (warn + "📚 KARUSEL (1-ci slayd). 2-ci slayd: sizin videonuz.\n\n" + e["headline"])[:900],
                                  "reply_markup": buttons("✅ Karusel paylaş", "pub", kid)}, files={"photo": f})
    else:
        pid = base + "-p"
        pending.append(dict(entry_common, id=pid, kind="post", image=str(img_path).replace("\\", "/")))
        with open(img_path, "rb") as f:
            tg_post("sendPhoto", {"caption": (warn + "🖼 FOTO POST\n\n" + e["headline"])[:900],
                                  "reply_markup": buttons("✅ Paylaş", "pub", pid)}, files={"photo": f})
    notify("📝 Instagram-da görünəcək mətn:\n\n" + caption)

    # --- 2) Reels ---
    if e["sensitive"] and not (photos or video):
        notify("ℹ️ Həssas mövzu və media yoxdur: bu xəbər üçün Reels hazırlanmadı.")
    else:
        rid = base + "-r"
        out = VIDEOS_DIR / (rid + ".mp4")
        inset = None
        if video:
            inset = {"path": video, "kind": "video", "credit": "", "keyword": "xəbərin videosu"}
        elif photos:
            inset = {"path": photos[0], "kind": "photo", "credit": "", "keyword": "xəbərin şəkli"}
        res = make_video(e["category"], e["headline"], e["summary"], "", str(out),
                         highlight=e.get("highlight", []), voiceover=e.get("voiceover", ""), inset=inset)
        if not res:
            notify("⚠️ Reels videosu hazırlanmadı (səs xətası və ya limit). Foto post hazırdır.")
        else:
            reel = dict(entry_common, id=rid, kind="reel", video=out.name)
            cover = VIDEOS_DIR / (rid + ".jpg")
            if photo:
                try:
                    make_cover_image(photo, e["category"], e["headline"], str(cover), highlight=e.get("highlight", []))
                    reel["cover"] = cover.name
                except Exception as ex:
                    print("  Örtük şəkli hazırlanmadı:", str(ex)[:100])
            if not reel.get("cover") and extract_frame(out, cover, 3):
                reel["cover"] = cover.name
            pending.append(reel)
            with open(out, "rb") as f:
                tg_post("sendVideo", {"caption": (warn + "🎬 REELS ÖNİZLƏMƏ (%d san.)\n\n%s" % (round(res["duration"]), e["headline"]))[:900],
                                      "supports_streaming": "true", "width": VW, "height": VH,
                                      "reply_markup": buttons("🎬 Reels paylaş", "reel", rid)}, files={"video": f})
    e["status"] = "built"
    e["raw_text"] = ""          # repo açıqdır: göndərdiyiniz xam mətn və fayl yolları saxlanılmır
    e["media"] = []


def main():
    if not PENDING_FILE.exists():
        print("pending.json yoxdur.")
        return
    pending = cleanup(json.loads(PENDING_FILE.read_text(encoding="utf-8")))
    todo = [p for p in pending if p.get("kind") == "news" and p.get("status") == "building"]
    if not todo:
        print("Hazırlanacaq xəbər yoxdur.")
        return
    for e in todo:
        print("Hazırlanır:", e["headline"])
        try:
            build_for(e, pending)
        except Exception as ex:
            e["status"] = "failed"
            print("  XƏTA:", str(ex)[:200])
            notify("⚠️ Xəbər hazırlanmadı: %s\n%s" % (e["headline"][:80], str(ex)[:200]))
        PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(2)


if __name__ == "__main__":
    main()
