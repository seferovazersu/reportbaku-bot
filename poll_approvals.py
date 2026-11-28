"""
ReportBaku - 4-cü addım: Telegram düymələrinə baxır və təsdiqlənən postları Instagram-a paylaşır.

Hər işə düşəndə:
  1. Telegram-dan yeni düymə basılmalarını oxuyur (yalnız sizin hesabınızdan olanlar qəbul edilir).
  2. [Ləğv et]  -> post "cancelled" olur.
  3. [Paylaş]   -> şəkil Instagram-a yüklənir və paylaşılır, Telegram mesajı yenilənir.
Eyni postu ikinci dəfə paylaşmaq mümkün deyil (status pending.json-da saxlanılır).
"""

import json
import re
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
IG_TOKEN = os.environ.get("INSTAGRAM_TOKEN", "")
IG_USER_ID = os.environ.get("INSTAGRAM_USER_ID", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("BRANCH", "main")
MEDIA_BRANCH = os.environ.get("MEDIA_BRANCH", "media")
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "24"))   # bundan köhnə xəbər paylaşılmır
PENDING_FILE = Path("pending.json")
GRAPH = "https://graph.facebook.com/"
SLOTS = [s.strip() for s in os.environ.get("SLOTS", "09:00,12:30,16:00,19:30,21:30").split(",") if s.strip()]  # Bakı vaxtı
MIN_GAP_MIN = 60          # planlaşdırılmış paylaşımlar arası minimum fasilə (dəq.)
SLOT_LEAD_MIN = 15        # ən tez neçə dəqiqə sonraya planlanır


class ApiError(Exception):
    pass


def http(method, url, params=None, timeout=60):
    """Sadə HTTP sorğusu. Xəta mətnlərində token görünməsin deyə URL yazılmır."""
    data = None
    if params is not None:
        enc = urllib.parse.urlencode(params)
        if method == "POST":
            data = enc.encode()
        else:
            url = url + "?" + enc
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise ApiError("HTTP %s: %s" % (e.code, body))
    except Exception as e:
        raise ApiError(type(e).__name__)


def tg(method, params):
    res = http("POST", "https://api.telegram.org/bot%s/%s" % (TG_TOKEN, method), params)
    return res.get("result")


def answer(cq, text):
    try:
        tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": text[:180]})
    except ApiError:
        pass  # çox köhnə düymələr üçün cavab vermək mümkün olmaya bilər


def finish_message(cq, status_line, markup=None):
    """Şəklin altındakı mətnin başına vəziyyət yazır və düymələri silir."""
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    mid = msg.get("message_id")
    if not (chat_id and mid):
        return
    try:
        if msg.get("caption") is not None or msg.get("photo") or msg.get("video"):
            caption = (status_line + "\n\n" + (msg.get("caption") or ""))[:1020]
            tg("editMessageCaption", {
                "chat_id": chat_id, "message_id": mid, "caption": caption,
                "reply_markup": json.dumps(markup or {"inline_keyboard": []}),
            })
        else:
            text = (status_line + "\n\n" + (msg.get("text") or ""))[:4000]
            tg("editMessageText", {
                "chat_id": chat_id, "message_id": mid, "text": text,
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(markup or {"inline_keyboard": []}),
            })
    except ApiError as e:
        print("  Mesaj yenilənmədi:", str(e)[:120])


def notify(text):
    try:
        tg("sendMessage", {"chat_id": TG_CHAT, "text": text[:3500]})
    except ApiError as e:
        print("  Bildiriş göndərilmədi:", str(e)[:120])


def _check_link(url):
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=0-99"})
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read(16)
            return
        except Exception:
            if i == 3:
                raise ApiError("Media linki açılmır")
            time.sleep(10)


def _wait_finished(cid, rounds):
    for _ in range(rounds):
        st = http("GET", GRAPH + cid, {"fields": "status_code,status", "access_token": IG_TOKEN})
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise ApiError("Instagram faylı qəbul etmədi (%s) %s" % (code, str(st.get("status", ""))[:150]))
        time.sleep(6)
    raise ApiError("Fayl vaxtında hazır olmadı")


def publish_carousel(post):
    """Karusel: şəkil(lər) + video (sağa sürüşdürməklə). {'ok':..., 'stage':..., 'error':...} qaytarır."""
    stage = "container"
    try:
        items = post.get("items") or (
            [{"type": "image", "path": i} for i in post.get("images", [])] +
            ([{"type": "video", "file": post["video"]}] if post.get("video") else []))
        children = []
        for it in items[:10]:
            if it["type"] == "image":
                url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, BRANCH, it["path"])
                params = {"image_url": url}
            else:
                url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, MEDIA_BRANCH, it["file"])
                params = {"media_type": "VIDEO", "video_url": url}
            _check_link(url)
            params.update({"is_carousel_item": "true", "access_token": IG_TOKEN})
            children.append(http("POST", GRAPH + IG_USER_ID + "/media", params)["id"])
        for cid in children:
            _wait_finished(cid, 60)
        res = http("POST", GRAPH + IG_USER_ID + "/media", {
            "media_type": "CAROUSEL", "children": ",".join(children),
            "caption": post["caption"], "access_token": IG_TOKEN})
        container = res["id"]
        _wait_finished(container, 30)
        stage = "publish"
        res = http("POST", GRAPH + IG_USER_ID + "/media_publish", {
            "creation_id": container, "access_token": IG_TOKEN})
        media_id = res["id"]
    except ApiError as e:
        return {"ok": False, "stage": stage, "error": str(e)}
    permalink = ""
    try:
        permalink = http("GET", GRAPH + media_id, {"fields": "permalink", "access_token": IG_TOKEN}).get("permalink", "")
    except ApiError:
        pass
    return {"ok": True, "media_id": media_id, "permalink": permalink}


def publish_story(post):
    """Story (24 saat): şəkil və ya video. Caption/link stikeri API ilə mümkün deyil."""
    stage = "container"
    try:
        if post.get("video"):
            url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, MEDIA_BRANCH, post["video"])
            params = {"media_type": "STORIES", "video_url": url}
        else:
            url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, BRANCH, post["image"])
            params = {"media_type": "STORIES", "image_url": url}
        _check_link(url)
        params["access_token"] = IG_TOKEN
        cid = http("POST", GRAPH + IG_USER_ID + "/media", params)["id"]
        _wait_finished(cid, 60)
        stage = "publish"
        media_id = http("POST", GRAPH + IG_USER_ID + "/media_publish", {
            "creation_id": cid, "access_token": IG_TOKEN})["id"]
    except ApiError as e:
        return {"ok": False, "stage": stage, "error": str(e)}
    return {"ok": True, "media_id": media_id, "permalink": ""}


def message_media(m):
    """Mesajdakı şəkil/videonu tapır: ('photo'|'video', file_id) və ya None."""
    if m.get("photo"):
        biggest = max(m["photo"], key=lambda x: (x.get("file_size") or 0, x.get("width", 0) * x.get("height", 0)))
        return "photo", biggest["file_id"]
    if m.get("video"):
        return "video", m["video"]["file_id"]
    d = m.get("document") or {}
    mime = d.get("mime_type", "")
    if mime.startswith("image/"):
        return "photo", d["file_id"]
    if mime.startswith("video/"):
        return "video", d["file_id"]
    return None


def tg_download(file_id, dest_dir, n):
    info = tg("getFile", {"file_id": file_id})
    path = info["file_path"]
    ext = os.path.splitext(path)[1] or ".bin"
    url = "https://api.telegram.org/file/bot%s/%s" % (TG_TOKEN, path)
    try:
        data = urllib.request.urlopen(url, timeout=180).read()
    except Exception as e:
        raise ApiError("yüklənmədi (%s)" % type(e).__name__)
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    dest = Path(dest_dir) / ("%d%s" % (n, ext))
    dest.write_bytes(data)
    return str(dest)


def cleanup_media(entry):
    for m in entry.get("media", []):
        if m.get("file"):
            try:
                Path(m["file"]).unlink()
            except OSError:
                pass


def new_custom_entry(text):
    now = datetime.now(timezone.utc)
    return {
        "id": (now + timedelta(hours=4)).strftime("%Y%m%d-%H%M%S") + "-u",
        "kind": "news", "created": now.isoformat(), "status": "awaiting_media", "custom": True,
        "raw_text": text[:3000], "media": [], "category": "CƏMİYYƏT", "headline": text[:90],
        "summary": "", "caption_text": "", "voiceover": "", "hashtags": [], "highlight": [],
        "source_note": "", "source_title": text[:120], "link": "", "sensitive": False,
    }


def handle_message(m, pending, by_id, touched):
    """Sizin göndərdiyiniz şəkil/video: ya yeni (özünüzün) xəbər, ya da gözləyən xəbərə əlavə olunur."""
    if str((m.get("from") or {}).get("id")) != str(TG_CHAT):
        return
    media = message_media(m)
    text = (m.get("caption") or m.get("text") or "").strip()
    if not media:
        if text and not text.startswith("/"):
            notify("ℹ️ Xəbər üçün şəkil və ya video göndərin. Öz xəbərinizi yaratmaq üçün şəkil/videonun altına "
                   "xəbər mətni (ən azı 40 simvol) yazın.")
        return
    kind, file_id = media
    gid = m.get("media_group_id")
    reply_to = (m.get("reply_to_message") or {}).get("message_id")
    target = None
    if reply_to:                                              # kartın cavabı kimi göndərilib
        target = next((p for p in pending if p.get("kind") == "news" and p.get("card_id") == reply_to), None)
    if target is None and gid:                                # eyni albomun əvvəlki şəkli
        target = next((p for p in pending if p.get("kind") == "news" and gid in p.get("groups", [])), None)
    if target is not None and target.get("status") == "awaiting_media":
        if gid and gid not in target.setdefault("groups", []):
            target["groups"].append(gid)
        touched["current"] = target
        entry = target
        text = ""
    elif len(text) >= 40:
        entry = new_custom_entry(text)
        if gid:
            entry["groups"] = [gid]
        pending.append(entry)
        by_id[entry["id"]] = entry
        touched["current"] = entry
    else:
        waiting = [p for p in pending if p.get("kind") == "news" and p.get("status") == "awaiting_media"]
        entry = touched.get("current")
        if entry is not None and not (entry.get("custom") or len(waiting) == 1):
            entry = None                      # bir neçə xəbər gözləyirsə, yalnız Reply ilə əlaqələndirilir
        if entry is None:
            if len(waiting) == 1:
                entry = waiting[0]
            elif len(waiting) > 1:
                notify("⚠️ Bir neçə xəbər gözləyir. Mediani aid olduğu xəbər kartına REPLY (cavab) edərək göndərin.")
                return
        if entry is None:
            notify("⚠️ Bu media hansı xəbərə aid olduğu bəlli deyil. 'Xeber topla' ilə xəbər gətirin və ya "
                   "mediaya xəbər mətni (ən azı 40 simvol) yazın.")
            return
        touched["current"] = entry
    try:
        info = tg("getFile", {"file_id": file_id})   # yalnız yoxlama (≤20 MB); yükləmə hazırlama zamanı olur
        if not info or not info.get("file_path"):
            raise ApiError("fayl tapılmadı")
    except ApiError as e:
        notify("⚠️ Fayl qəbul edilmədi: %s (Telegram botlar üçün fayl ölçüsü 20 MB-dan çox ola bilməz)" % str(e)[:150])
        return
    entry["media"].append({"file_id": file_id, "type": kind})
    touched.setdefault("entries", {})[entry["id"]] = entry


def send_touch_messages(touched):
    """Media əlavə olunan hər xəbər üçün düymələrlə mesaj: ✅ Hazırla, 🔊 Orijinal səslə (videoda), ❌ Ləğv et."""
    for e in (touched.get("entries") or {}).values():
        n = len(e["media"])
        vids = sum(1 for m in e["media"] if m["type"] == "video")
        rows = [[{"text": "✅ Hazırla", "callback_data": "build:" + e["id"]}]]
        if vids:
            rows[0].append({"text": "🔊 Orijinal səslə", "callback_data": "buildo:" + e["id"]})
        if not e.get("custom"):
            rows.append([{"text": "⏭ Mediasız", "callback_data": "skip:" + e["id"]}])
        rows.append([{"text": "❌ Ləğv et", "callback_data": "del:" + e["id"]}])
        head = "📝 Sizin xəbəriniz qəbul edildi:\n\n%s\n\n" % e["raw_text"][:400] if (e.get("custom") and not e.get("card_sent")) else ""
        e["card_sent"] = True
        try:
            tg("sendMessage", {"chat_id": TG_CHAT, "reply_markup": json.dumps({"inline_keyboard": rows}),
                               "text": head + "📎 %d fayl (%d video) əlavə olundu. Seçin:" % (n, vids)})
        except ApiError as ex:
            print("  Düymə mesajı göndərilmədi:", str(ex)[:100])


def handle_news_action(cq, action, e, pending):
    if e["status"] != "awaiting_media":
        answer(cq, "Bu xəbər artıq: " + e["status"])
        return
    if action == "del":
        e["status"] = "cancelled"
        cleanup_media(e)
        e["raw_text"] = ""
        e["media"] = []
        save(pending)
        answer(cq, "Ləğv edildi.")
        finish_message(cq, "❌ LƏĞV EDİLDİ")
        return
    if action == "buildo":
        if not any(m["type"] == "video" for m in e["media"]):
            answer(cq, "Orijinal səs üçün əvvəl video göndərin (karta Reply ilə).")
            return
        e["original_audio"] = True
        action = "build"
    if action == "build" and not e["media"]:
        answer(cq, "Əvvəl şəkil/video göndərin, ya da ⏭ Mediasız hazırla basın.")
        return
    if action == "skip":
        cleanup_media(e)
        e["media"] = []
    if action in ("build", "skip"):
        e["status"] = "building"
        save(pending)
        answer(cq, "Hazırlanır...")
        finish_message(cq, "⏳ HAZIRLANIR (%d fayl). Nəticə bir neçə dəqiqəyə Telegram-a gələcək." % len(e["media"]))
        return
    answer(cq, "Naməlum əmr.")


def slim_msg(cq):
    m = cq.get("message") or {}
    return {"message_id": m.get("message_id"), "chat": {"id": (m.get("chat") or {}).get("id")},
            "caption": m.get("caption"), "text": m.get("text"),
            "photo": bool(m.get("photo")), "video": bool(m.get("video"))}


def next_slot(pending):
    """Növbəti boş paylaşım vaxtı (UTC). SCHEDULE=0 olarsa None (dərhal paylaşılır)."""
    if os.environ.get("SCHEDULE", "0") != "1" or not SLOTS:   # planlaşdırma standart olaraq söndürülüb
        return None
    now = datetime.now(timezone.utc)
    taken = [datetime.fromisoformat(x["publish_at"]) for x in pending
             if x.get("status") == "scheduled" and x.get("publish_at")]
    baku_today = (now + timedelta(hours=4)).date()
    for day in range(3):
        d = baku_today + timedelta(days=day)
        for t in sorted(SLOTS):
            try:
                h, m = [int(v) for v in t.split(":")]
            except ValueError:
                continue
            cand = (datetime(d.year, d.month, d.day, h, m) - timedelta(hours=4)).replace(tzinfo=timezone.utc)
            if cand < now + timedelta(minutes=SLOT_LEAD_MIN):
                continue
            if all(abs((cand - x).total_seconds()) >= MIN_GAP_MIN * 60 for x in taken):
                return cand
    return None


SOURCE_NAMES = ["AZƏRTAC", "AZERTAC", "APA", "Trend", "Trend.az", "Report", "Report.az", "Day.az", "Oxu.az", "Oxu",
                "Publika", "Sia.az", "Sia", "Qafqazinfo", "Xəzər Xəbər", "Haqqin.az", "Metbuat.az", "Minval.az",
                "Baku TV", "Baku.ws", "AzTV", "İctimai TV", "Musavat", "Milli.az", "1news", "Modern.az",
                "Interfax", "BBC", "Azadlıq", "Sputnik", "Reuters", "AP", "Xinhua", "Interfax.az"]
SOURCE_RX = re.compile(r"(?<![\w.])(" + "|".join(re.escape(x) for x in sorted(SOURCE_NAMES, key=len, reverse=True)) + r")(?![\w.])", re.IGNORECASE)
SOURCE_PHRASE_RX = re.compile(r"\b(məlumatına görə|xəbər verir|xəbər verib|məlumat verir|yazır ki)\b", re.IGNORECASE)


def scrub_sources(text):
    """Mənbə adı və ya mənbə ifadəsi olan CÜMLƏNİ tamamilə çıxarır (cümlə pozulmasın deyə)."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    kept = [s for s in parts if s.strip() and not SOURCE_RX.search(s) and not SOURCE_PHRASE_RX.search(s)]
    out = " ".join(kept).strip()
    return out or re.sub(r"\s+", " ", text or "").strip()


def publish_telegram_channel(post):
    """Eyni paylaşımı Telegram kanalına göndərir, mesajın linkini post["tg_link"]-ə yazır."""
    chan = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    if not chan or post.get("kind") == "story":
        return ""
    base = post.get("tg_caption") or post.get("headline") or ""
    cap = scrub_sources(base)[:1000]
    msg_id = None
    try:
        if post.get("kind") == "carousel":
            media = []
            for it in (post.get("items") or [])[:10]:
                if it["type"] == "image":
                    url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, BRANCH, it["path"])
                    media.append({"type": "photo", "media": url})
                else:
                    url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, MEDIA_BRANCH, it["file"])
                    media.append({"type": "video", "media": url})
            if media:
                media[0]["caption"] = cap
            r = tg("sendMediaGroup", {"chat_id": chan, "media": json.dumps(media)})
            msg_id = (r[0] if isinstance(r, list) and r else {}).get("message_id")
        elif post.get("kind") == "reel":
            url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, MEDIA_BRANCH, post["video"])
            r = tg("sendVideo", {"chat_id": chan, "video": url, "caption": cap, "supports_streaming": "true"})
            msg_id = (r or {}).get("message_id")
        else:
            url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, BRANCH, post["image"])
            r = tg("sendPhoto", {"chat_id": chan, "photo": url, "caption": cap})
            msg_id = (r or {}).get("message_id")
    except ApiError as e:
        print("  Kanal xətası:", str(e)[:150])
        return "\n⚠️ Telegram kanalına göndərilmədi (bot admin deyil?)"
    if msg_id:
        post["tg_link"] = "https://t.me/%s/%s" % (chan.lstrip("@"), msg_id)
        return "\n📣 Telegram kanalına da göndərildi"
    return "\n⚠️ Kanal mesajı nömrəsi alınmadı"


def run_publish(post, kind, pending, msg):
    """Postu dərhal paylaşır və Telegram mesajını yeniləyir."""
    cq = {"message": msg}
    post["status"] = "publishing"
    save(pending)  # ikiqat paylaşımın qarşısını almaq üçün əvvəlcədən yazırıq
    tg_note = publish_telegram_channel(post) if post.get("kind") != "story" else ""
    if post.get("tg_link") and post.get("caption"):
        if "📲 Ətraflı və tam xəbər:" in post["caption"]:
            post["caption"] = re.sub(r"📲 Ətraflı və tam xəbər: \S+",
                                     "📲 Ətraflı və tam xəbər: " + post["tg_link"], post["caption"], count=1)
        else:
            post["caption"] += "\n\n📲 Ətraflı və tam xəbər: " + post["tg_link"]
    result = publish(post)
    if result["ok"]:
        post["status"] = "published"
        post["published_at"] = datetime.now(timezone.utc).isoformat()
        post["instagram_id"] = result["media_id"]
        post["permalink"] = result["permalink"]
        save(pending)
        line = {"reel": "✅ REELS PAYLAŞILDI", "carousel": "✅ KARUSEL PAYLAŞILDI",
                "story": "✅ STORY PAYLAŞILDI"}.get(kind, "✅ PAYLAŞILDI")
        if result["permalink"]:
            line += "\n" + result["permalink"]
        line += tg_note
        finish_message(cq, line)
    elif result["stage"] == "container":
        post["status"] = "pending"   # hələ paylaşılmayıb, yenidən cəhd etmək olar
        post.pop("publish_at", None)
        save(pending)
        notify("⚠️ Paylaşım alınmadı: %s\nDüyməyə yenidən basa bilərsiniz.\n\n%s" % (result["error"], post["headline"]))
    else:
        post["status"] = "check"      # paylaşılıb-paylaşılmadığı bəlli deyil
        save(pending)
        finish_message(cq, "⚠️ NƏTİCƏ BƏLLİ DEYİL: Instagram-ı əl ilə yoxlayın")
        notify("⚠️ Paylaşım əmri xəta ilə bitdi: %s\nİkiqat paylaşımın qarşısını almaq üçün bu post bağlandı. "
               "Instagram profilinə baxıb yoxlayın.\n\n%s" % (result["error"], post["headline"]))


def run_scheduled(pending):
    now = datetime.now(timezone.utc)
    for post in pending:
        if post.get("status") != "scheduled" or not post.get("publish_at"):
            continue
        if datetime.fromisoformat(post["publish_at"]) > now:
            continue
        if now - datetime.fromisoformat(post["created"]) > timedelta(hours=MAX_AGE_HOURS + 6):
            post["status"] = "expired"
            save(pending)
            finish_message({"message": post.get("msg") or {}}, "⏱ VAXTI KEÇİB")
            continue
        print("Planlaşdırılmış paylaşım: %s" % post["id"])
        run_publish(post, post.get("kind", "post"), pending, post.get("msg") or {})


def publish(post):
    """Şəkli və ya Reels videosunu Instagram-a paylaşır.
    {'ok':..., 'stage':..., 'error':..., 'permalink':...} qaytarır."""
    if post.get("kind") == "carousel":
        return publish_carousel(post)
    if post.get("kind") == "story":
        return publish_story(post)
    is_reel = post.get("kind") == "reel"
    if is_reel:
        media_url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, MEDIA_BRANCH, post["video"])
        params = {"media_type": "REELS", "video_url": media_url, "share_to_feed": "true"}
        if post.get("cover"):       # örtük şəkli (videonun 3-cü saniyəsindən çıxarılıb)
            params["cover_url"] = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, MEDIA_BRANCH, post["cover"])
        else:
            params["thumb_offset"] = "3000"
        wait_rounds = 60        # videonu emal etmək üçün ~6 dəqiqəyə qədər gözləyirik
    else:
        media_url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, BRANCH, post["image"])
        params = {"image_url": media_url}
        wait_rounds = 10
    stage = "container"
    try:
        for i in range(4):  # link açılırmı
            try:
                req = urllib.request.Request(media_url, headers={"Range": "bytes=0-99"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    r.read(16)
                break
            except Exception:
                if i == 3:
                    raise ApiError("Media linki açılmır")
                time.sleep(10)

        params.update({"caption": post["caption"], "access_token": IG_TOKEN})
        res = http("POST", GRAPH + IG_USER_ID + "/media", params)
        cid = res["id"]

        for _ in range(wait_rounds):
            st = http("GET", GRAPH + cid, {"fields": "status_code,status", "access_token": IG_TOKEN})
            code = st.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise ApiError("Instagram faylı qəbul etmədi (%s) %s" % (code, str(st.get("status", ""))[:150]))
            time.sleep(6)
        else:
            raise ApiError("Fayl vaxtında hazır olmadı")

        stage = "publish"
        res = http("POST", GRAPH + IG_USER_ID + "/media_publish", {
            "creation_id": cid, "access_token": IG_TOKEN})
        media_id = res["id"]
    except ApiError as e:
        return {"ok": False, "stage": stage, "error": str(e)}

    permalink = ""
    try:
        info = http("GET", GRAPH + media_id, {"fields": "permalink", "access_token": IG_TOKEN})
        permalink = info.get("permalink", "")
    except ApiError:
        pass
    return {"ok": True, "media_id": media_id, "permalink": permalink}


def save(pending):
    PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram ayarları yoxdur.")
        raise SystemExit(1)

    updates = tg("getUpdates", {"allowed_updates": json.dumps(["callback_query", "message"]), "limit": 100}) or []
    if not updates:
        print("Yeni düymə basılması və ya mesaj yoxdur.")
        return

    pending = json.loads(PENDING_FILE.read_text(encoding="utf-8")) if PENDING_FILE.exists() else []
    by_id = {p["id"]: p for p in pending}
    max_update = 0
    touched = {}

    for u in updates:
        max_update = max(max_update, u["update_id"])
        if u.get("message"):
            handle_message(u["message"], pending, by_id, touched)
            continue
        cq = u.get("callback_query")
        if not cq:
            continue
        if str((cq.get("from") or {}).get("id")) != str(TG_CHAT):
            answer(cq, "İcazə yoxdur.")
            continue

        action, _, post_id = (cq.get("data") or "").partition(":")
        post = by_id.get(post_id)
        print("Düymə: %s %s" % (action, post_id))
        if not post:
            answer(cq, "Bu post tapılmadı.")
            finish_message(cq, "❔ Qeyd tapılmadı (köhnə post ola bilər)")
            continue
        if post.get("kind") == "news":
            handle_news_action(cq, action, post, pending)
            continue
        if post["status"] not in ("pending", "scheduled"):
            answer(cq, "Bu post artıq: " + post["status"])
            finish_message(cq, "ℹ️ Bu post artıq emal olunub (%s)" % post["status"])
            continue

        if action == "del":
            post["status"] = "cancelled"
            save(pending)
            answer(cq, "Ləğv edildi.")
            finish_message(cq, "❌ LƏĞV EDİLDİ")
            continue

        kind = post.get("kind", "post")
        if action == "reelo":
            if kind != "reel" or not post.get("src_video_file_id"):
                answer(cq, "Bu Reels üçün orijinal səs seçimi yoxdur.")
                continue
            post["status"] = "rebuild_orig"
            save(pending)
            answer(cq, "Orijinal səslə hazırlanır...")
            finish_message(cq, "🔊 ORİJİNAL SƏSLƏ HAZIRLANIR... (bir neçə dəqiqə)")
            continue
        if (action == "pub" and kind not in ("post", "carousel")) or (action == "reel" and kind != "reel") \
                or (action == "story" and kind != "story") or action not in ("pub", "reel", "story", "now"):
            answer(cq, "Naməlum əmr.")
            continue
        if post["status"] == "scheduled" and action != "now":
            answer(cq, "Artıq planlaşdırılıb. ⚡ İndi və ya ❌ ilə dəyişə bilərsiniz.")
            continue

        # --- Paylaşım ---
        age = datetime.now(timezone.utc) - datetime.fromisoformat(post["created"])
        if age > timedelta(hours=MAX_AGE_HOURS):
            post["status"] = "expired"
            save(pending)
            answer(cq, "Vaxtı keçib.")
            finish_message(cq, "⏱ VAXTI KEÇİB (%d saatdan çox)" % MAX_AGE_HOURS)
            continue

        if action != "now":
            slot = next_slot(pending)
            if slot and (slot - datetime.fromisoformat(post["created"])) <= timedelta(hours=MAX_AGE_HOURS):
                post["status"] = "scheduled"
                post["publish_at"] = slot.isoformat()
                post["msg"] = slim_msg(cq)
                save(pending)
                answer(cq, "Planlaşdırıldı.")
                finish_message(cq, "🕒 PLANLAŞDIRILDI: %s (Bakı vaxtı)" % (slot + timedelta(hours=4)).strftime("%d.%m %H:%M"),
                               {"inline_keyboard": [[{"text": "⚡ İndi paylaş", "callback_data": "now:" + post["id"]},
                                                     {"text": "❌ Ləğv et", "callback_data": "del:" + post["id"]}]]})
                continue
        answer(cq, "Reels paylaşılır..." if kind == "reel" else "Paylaşılır...")
        run_publish(post, kind, pending, slim_msg(cq))

    # emal olunmuş düymələri Telegram-dan silmək üçün təsdiqləyirik
    tg("getUpdates", {"offset": max_update + 1, "limit": 1,
                      "allowed_updates": json.dumps(["callback_query", "message"])})
    save(pending)
    run_scheduled(pending)
    send_touch_messages(touched)
    save(pending)
    if any((p.get("kind") == "news" and p.get("status") == "building") or p.get("status") == "rebuild_orig" for p in pending):
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write("has_work=true\n")
        print("Hazırlanacaq xəbər var (ikinci mərhələ işə düşəcək).")


if __name__ == "__main__":
    main()
