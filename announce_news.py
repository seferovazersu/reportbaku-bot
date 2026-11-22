"""
ReportBaku - yeni axın, 1-ci addım: tapılan xəbəri Telegram-a "xəbər kartı" kimi göndərir.

post_drafts.json-dakı İLK xəbər götürülür (hər işləmədə yalnız 1 xəbər). Siz:
  1) linki açıb xəbərə uyğun şəkil/video tapırsınız,
  2) onu bu söhbətə göndərirsiniz (şəkil, video, və ya hər ikisi),
  3) "✅ Hazırla" düyməsinə basırsınız (şəkil/videosuz üçün "⏭ Mediasız hazırla", lazım deyilsə "❌ Ləğv et").
Şəkil postu, karusel və Reels-i sonra avtomatik hazırlayıb təsdiqinizə göndəririk.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

IN_FILE = Path("post_drafts.json")
PENDING_FILE = Path("pending.json")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


def card_text(e):
    warn = "⚠️ HƏSSAS MÖVZU: şəkil/video seçəndə və paylaşmazdan əvvəl diqqətli olun\n\n" if e["sensitive"] else ""
    return (
        "📰 YENİ XƏBƏR (media gözləyir)\n\n%s%s\n\n%s\n\n%s\n%s\n\n"
        "1) Linki açın, xəbərə uyğun şəkil və ya video tapın (müəllif hüququna diqqət edin).\n"
        "2) Onu bu söhbətə göndərin (şəkil, video, və ya hər ikisi).\n"
        "3) ✅ Hazırla basın. Media istəmirsinizsə ⏭ Mediasız hazırla, xəbər lazım deyilsə ❌ Ləğv et."
    ) % (warn, e["headline"], e["summary"], e.get("source_note", ""), e.get("link", ""))


def send_card(e):
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram ayarları yoxdur.")
        return
    markup = {"inline_keyboard": [
        [{"text": "✅ Hazırla", "callback_data": "build:" + e["id"]},
         {"text": "⏭ Mediasız hazırla", "callback_data": "skip:" + e["id"]}],
        [{"text": "❌ Ləğv et", "callback_data": "del:" + e["id"]}],
    ]}
    r = requests.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                      data={"chat_id": TG_CHAT, "text": card_text(e)[:4000],
                            "disable_web_page_preview": "true", "reply_markup": json.dumps(markup)},
                      timeout=60)
    if not r.ok:
        print("Telegram xətası:", r.status_code, r.text[:200])


def main():
    if not IN_FILE.exists():
        print("post_drafts.json yoxdur.")
        raise SystemExit(1)
    drafts = json.loads(IN_FILE.read_text(encoding="utf-8"))
    if not drafts:
        print("Bu dəfə uyğun xəbər tapılmadı (təkrar, reklam və ya məlumat azlığı).")
        if TG_TOKEN and TG_CHAT:
            requests.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                          data={"chat_id": TG_CHAT, "text": "ℹ️ Bu dəfə yeni uyğun xəbər tapılmadı. Bir az sonra yenidən 'Xeber topla' işə salın."},
                          timeout=60)
        return
    d = drafts[0]
    now = datetime.now(timezone.utc)
    stamp = (now + timedelta(hours=4)).strftime("%Y%m%d-%H%M%S")
    pending = json.loads(PENDING_FILE.read_text(encoding="utf-8")) if PENDING_FILE.exists() else []
    entry = {
        "id": stamp + "-01",
        "kind": "news",
        "created": now.isoformat(),
        "status": "awaiting_media",
        "custom": False,
        "media": [],
        "category": d["category"],
        "headline": d["headline"],
        "summary": d["summary"],
        "caption_text": d.get("caption_text", ""),
        "voiceover": d.get("voiceover", ""),
        "hashtags": d.get("hashtags", []),
        "highlight": d.get("highlight", []),
        "source_note": d.get("source_note", ""),
        "source_title": d.get("source_title", ""),
        "link": d.get("link", ""),
        "sensitive": bool(d.get("sensitive")),
    }
    pending.append(entry)
    PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    send_card(entry)
    print("Xəbər kartı göndərildi:", entry["headline"])


if __name__ == "__main__":
    main()
