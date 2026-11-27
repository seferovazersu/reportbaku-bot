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
        {"text": "⚡ İndi", "callback_data": "now:" + id_}],
        [{"text": "❌ Ləğv et", "callback_data": "del:" + id_}]]})


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
    """Karusel üçün video: 4:5 (1080x1350), kəsilmədən sığdırılır (bulanıq fon), H.264/AAC, ≤58 san."""
    flt = ("[0:v]split[a][b];[a]scale=1080:1350:force_original_aspect_ratio=increase,crop=1080:1350,boxblur=25:3[bg];"
           "[b]scale=1080:1350:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p")
    return run_ffmpeg(["-i", str(src), "-t", "58", "-filter_complex", flt, "-r", "30",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                       "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])


def photo_45(src, out):
    """Karusel üçün şəkil: 4:5 (1080x1350), kəsilmədən sığdırılır."""
    from PIL import Image
    from build_video import fit_blur
    fit_blur(Image.open(src), (1080, 1350)).save(out, quality=92)
    return True


def montage(items, out, photo_secs=4, video_secs=8):
    """Bir neçə şəkil/videonu ardıcıl göstərən tam eni klip (Reels pəncərəsi üçün). items: [(yol, 'photo'|'video')]."""
    W, H = 1080, 1060
    flt = ("[0:v]split[a][b];[a]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,boxblur=25:3[bg];"
           "[b]scale=%d:%d:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=24,format=yuv420p"
           % (W, H, W, H, W, H))
    folder = Path(out).parent
    segs = []
    for i, (path, typ) in enumerate(items):
        seg = folder / ("seg%d.mp4" % i)
        pre = ["-loop", "1", "-t", str(photo_secs), "-i", str(path)] if typ == "photo" else ["-t", str(video_secs), "-i", str(path)]
        if run_ffmpeg(pre + ["-filter_complex", flt, "-an", "-c:v", "libx264", "-preset", "veryfast",
                             "-crf", "23", "-r", "24", str(seg)]):
            segs.append(seg)
    if not segs:
        return False
    lst = folder / "list.txt"
    lst.write_text("".join("file '%s'\n" % s.resolve() for s in segs), encoding="utf-8")
    return run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])


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
    e["tg_caption"] = str(res.get("tg_caption", "")).strip()[:400]
    e["voiceover"] = str(res.get("voiceover", "")).strip()[:1100]
    e["category"] = str(res.get("category", "CƏMİYYƏT")).strip().upper() or "CƏMİYYƏT"
    e["hashtags"] = [h for h in (res.get("hashtags") or []) if isinstance(h, str) and h.startswith("#")][:5]
    e["highlight"] = [w for w in (res.get("highlight") or []) if isinstance(w, str) and w in e["headline"]][:3]
    e["sensitive"] = bool(res.get("sensitive"))
    return bool(e["headline"] and e["summary"])


def download_media(e):
    """Telegram-dakı faylları (file_id ilə) bu işləmənin qovluğuna yükləyir."""
    folder = INCOMING / e["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for n, m in enumerate(e.get("media", []), 1):
        if m.get("file") and Path(m["file"]).exists():
            continue
        r = requests.post("https://api.telegram.org/bot%s/getFile" % TG_TOKEN, data={"file_id": m["file_id"]}, timeout=60)
        info = r.json().get("result") if r.ok else None
        if not info:
            raise RuntimeError("Telegram faylı tapılmadı (fayl ölçüsü 20 MB-dan çox ola bilər)")
        data = requests.get("https://api.telegram.org/file/bot%s/%s" % (TG_TOKEN, info["file_path"]), timeout=180).content
        ext = os.path.splitext(info["file_path"])[1] or (".jpg" if m["type"] == "photo" else ".mp4")
        path = folder / ("%d%s" % (n, ext))
        path.write_bytes(data)
        m["file"] = str(path)


def build_for(e, pending):
    download_media(e)
    if e.get("custom") and not e.get("summary"):
        if not fill_from_text(e):
            raise RuntimeError("Mətndən xəbər hazırlanmadı (AI xülasə yaza bilmədi)")

    photos = [m["file"] for m in e.get("media", []) if m["type"] == "photo" and Path(m["file"]).exists()]
    videos = [m["file"] for m in e.get("media", []) if m["type"] == "video" and Path(m["file"]).exists()]
    if e.get("media") and not (photos or videos):
        raise RuntimeError("Göndərdiyiniz media yüklənmədi, yenidən göndərin")
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
    # --- 1) şəkil postu (və ya karusel: birinci slayd + sizin bütün şəkil/videolarınız) ---
    img_path = POSTS_DIR / (base + "-p.jpg")
    make_post(photo, e["category"], e["headline"], e["summary"], str(img_path), variant=variant,
              highlight=e.get("highlight", []), source_note="")
    entry_common = {"created": datetime.now(timezone.utc).isoformat(), "status": "pending",
                    "category": e["category"], "headline": e["headline"], "summary": e["summary"],
                    "source_note": e.get("source_note", ""), "link": e.get("link", ""),
                    "sensitive": e["sensitive"], "caption": caption, "tg_caption": e.get("tg_caption", "")}
    warn = "⚠️ HƏSSAS MÖVZU: paylaşmazdan əvvəl yoxlayın\n\n" if e["sensitive"] else ""
    extra_photos = photos[1:] if photos else []
    items = [{"type": "image", "path": str(img_path).replace("\\", "/")}]
    for n, ph in enumerate(extra_photos, 2):
        pth = POSTS_DIR / ("%s-c%d.jpg" % (base, n))
        if photo_45(ph, pth):
            items.append({"type": "image", "path": str(pth).replace("\\", "/")})
    for n, vd in enumerate(videos, 1):
        name = "%s-k%d.mp4" % (base, n)
        if transcode_carousel(vd, VIDEOS_DIR / name):
            items.append({"type": "video", "file": name})
    items = items[:10]
    if len(items) > 1:
        kid = base + "-k"
        pending.append(dict(entry_common, id=kid, kind="carousel", items=items))
        with open(img_path, "rb") as f:
            tg_post("sendPhoto", {"caption": (warn + "📚 KARUSEL (%d slayd: 1-ci slayd bu, qalanları sizin media)\n\n%s" % (len(items), e["headline"]))[:900],
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
        ordered = [(m["file"], m["type"]) for m in e.get("media", []) if m.get("file") and Path(m["file"]).exists()]
        mont = INCOMING / e["id"] / "montage.mp4"
        if e.get("original_audio") and video:
            inset = {"path": video, "kind": "video", "credit": "", "keyword": "xəbərin videosu"}
        elif len(ordered) >= 2 and montage(ordered, mont):
            inset = {"path": str(mont), "kind": "video", "credit": "", "keyword": "xəbərin mediası (ardıcıl)"}
        elif video:
            inset = {"path": video, "kind": "video", "credit": "", "keyword": "xəbərin videosu"}
        elif photos:
            inset = {"path": photos[0], "kind": "photo", "credit": "", "keyword": "xəbərin şəkli"}
        res = make_video(e["category"], e["headline"], e["summary"], "", str(out),
                         highlight=e.get("highlight", []), voiceover=e.get("voiceover", ""), inset=inset,
                         audio_from=video if (e.get("original_audio") and video) else None)
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
                tg_post("sendVideo", {"caption": (warn + "🎬 REELS ÖNİZLƏMƏ (%d san., səs: %s)\n\n%s" % (round(res["duration"]), res["model"], e["headline"]))[:900],
                                      "supports_streaming": "true", "width": VW, "height": VH,
                                      "reply_markup": buttons("🎬 Reels paylaş", "reel", rid)}, files={"video": f})
    # --- 3) Story (9:16 şəkil: media tam ekran + başlıq) ---
    sid = base + "-s"
    story_img = POSTS_DIR / (sid + ".jpg")
    try:
        make_cover_image(photo, e["category"], e["headline"], str(story_img), highlight=e.get("highlight", []))
        pending.append(dict(entry_common, id=sid, kind="story", image=str(story_img).replace("\\", "/")))
        with open(story_img, "rb") as f:
            tg_post("sendPhoto", {"caption": (warn + "📲 STORY (24 saat)\n\n" + e["headline"])[:900],
                                  "reply_markup": buttons("📲 Story paylaş", "story", sid)}, files={"photo": f})
    except Exception as ex:
        print("  Story hazırlanmadı:", str(ex)[:100])
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
