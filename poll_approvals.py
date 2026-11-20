"""
ReportBaku - 4-cü addım: Telegram düymələrinə baxır və təsdiqlənən postları Instagram-a paylaşır.

Hər işə düşəndə:
  1. Telegram-dan yeni düymə basılmalarını oxuyur (yalnız sizin hesabınızdan olanlar qəbul edilir).
  2. [Ləğv et]  -> post "cancelled" olur.
  3. [Paylaş]   -> şəkil Instagram-a yüklənir və paylaşılır, Telegram mesajı yenilənir.
Eyni postu ikinci dəfə paylaşmaq mümkün deyil (status pending.json-da saxlanılır).
"""

import json
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
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "12"))   # bundan köhnə xəbər paylaşılmır
PENDING_FILE = Path("pending.json")
GRAPH = "https://graph.facebook.com/"


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


def finish_message(cq, status_line):
    """Şəklin altındakı mətnin başına vəziyyət yazır və düymələri silir."""
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    mid = msg.get("message_id")
    if not (chat_id and mid):
        return
    caption = (status_line + "\n\n" + (msg.get("caption") or ""))[:1020]
    try:
        tg("editMessageCaption", {
            "chat_id": chat_id, "message_id": mid, "caption": caption,
            "reply_markup": json.dumps({"inline_keyboard": []}),
        })
    except ApiError as e:
        print("  Mesaj yenilənmədi:", str(e)[:120])


def notify(text):
    try:
        tg("sendMessage", {"chat_id": TG_CHAT, "text": text[:3500]})
    except ApiError as e:
        print("  Bildiriş göndərilmədi:", str(e)[:120])


def publish(post):
    """Şəkli və ya Reels videosunu Instagram-a paylaşır.
    {'ok':..., 'stage':..., 'error':..., 'permalink':...} qaytarır."""
    is_reel = post.get("kind") == "reel"
    if is_reel:
        media_url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, MEDIA_BRANCH, post["video"])
        params = {"media_type": "REELS", "video_url": media_url, "share_to_feed": "true",
                  "thumb_offset": "3000"}   # örtük: 3-cü saniyənin kadrı (başlıq və görüntü artıq görünür)
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

    updates = tg("getUpdates", {"allowed_updates": json.dumps(["callback_query"]), "limit": 100}) or []
    if not updates:
        print("Yeni düymə basılması yoxdur.")
        return

    pending = json.loads(PENDING_FILE.read_text(encoding="utf-8")) if PENDING_FILE.exists() else []
    by_id = {p["id"]: p for p in pending}
    max_update = 0

    for u in updates:
        max_update = max(max_update, u["update_id"])
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
        if post["status"] != "pending":
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
        if (action == "pub" and kind != "post") or (action == "reel" and kind != "reel") \
                or action not in ("pub", "reel"):
            answer(cq, "Naməlum əmr.")
            continue

        # --- Paylaşım ---
        age = datetime.now(timezone.utc) - datetime.fromisoformat(post["created"])
        if age > timedelta(hours=MAX_AGE_HOURS):
            post["status"] = "expired"
            save(pending)
            answer(cq, "Vaxtı keçib.")
            finish_message(cq, "⏱ VAXTI KEÇİB (%d saatdan çox)" % MAX_AGE_HOURS)
            continue

        post["status"] = "publishing"
        save(pending)  # ikiqat paylaşımın qarşısını almaq üçün əvvəlcədən yazırıq
        answer(cq, "Reels paylaşılır..." if kind == "reel" else "Paylaşılır...")
        result = publish(post)

        if result["ok"]:
            post["status"] = "published"
            post["instagram_id"] = result["media_id"]
            post["permalink"] = result["permalink"]
            save(pending)
            line = "✅ REELS PAYLAŞILDI" if kind == "reel" else "✅ PAYLAŞILDI"
            if result["permalink"]:
                line += "\n" + result["permalink"]
            finish_message(cq, line)
        elif result["stage"] == "container":
            post["status"] = "pending"   # hələ paylaşılmayıb, yenidən cəhd etmək olar
            save(pending)
            notify("⚠️ Paylaşım alınmadı: %s\nDüyməyə yenidən basa bilərsiniz.\n\n%s"
                   % (result["error"], post["headline"]))
        else:
            post["status"] = "check"      # paylaşılıb-paylaşılmadığı bəlli deyil
            save(pending)
            finish_message(cq, "⚠️ NƏTİCƏ BƏLLİ DEYİL: Instagram-ı əl ilə yoxlayın")
            notify("⚠️ Paylaşım əmri xəta ilə bitdi: %s\nİkiqat paylaşımın qarşısını almaq üçün "
                   "bu post bağlandı. Instagram profilinə baxıb yoxlayın.\n\n%s"
                   % (result["error"], post["headline"]))

    # emal olunmuş düymələri Telegram-dan silmək üçün təsdiqləyirik
    tg("getUpdates", {"offset": max_update + 1, "limit": 1,
                      "allowed_updates": json.dumps(["callback_query"])})
    save(pending)


if __name__ == "__main__":
    main()
