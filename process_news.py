"""
ReportBaku - 2-ci addım: seçilmiş xəbərləri AI ilə post mətninə çevirmək.

selected_news.json  ->  post_drafts.json
Hər xəbər üçün: kateqoriya, qısa başlıq, 2-3 cümləlik xülasə, vurğulanacaq sözlər, mənbə.
Hazır mətnlər baxış üçün Telegram-a da göndərilir (hələlik yalnız mətn, düymə yoxdur).
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

# ---------------- Ayarlar ----------------
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
API_KEY = os.environ.get("GEMINI_API_KEY", "")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

IN_FILE = Path("selected_news.json")
OUT_FILE = Path("post_drafts.json")
USER_AGENT = "Mozilla/5.0 (compatible; ReportBakuBot/1.0)"
PAUSE_SECONDS = 6          # Gemini-nin pulsuz limitinə düşməmək üçün sorğular arası fasilə
MIN_TEXT = 100             # bundan qısa mətndən xülasə yazmırıq

CATEGORIES = [
    "SİYASƏT", "İQTİSADİYYAT", "CƏMİYYƏT", "HADİSƏ",
    "DÜNYA", "İDMAN", "MƏDƏNİYYƏT", "TEXNOLOGİYA", "SAĞLAMLIQ",
]

PROMPT = (
    "Sən ReportBaku adlı Azərbaycan xəbər səhifəsinin redaktorusan. "
    "Aşağıdakı mənbə mətninə əsasən Instagram postu üçün mətn hazırla.\n\n"
    "Qaydalar:\n"
    "1. Yalnız mənbə mətnində olan faktlardan istifadə et. Heç nə əlavə etmə, təxmin etmə, "
    "rəqəmləri, adları və tarixləri dəyişmə.\n"
    "2. Mənbənin cümlələrini köçürmə. Hər şeyi öz sözlərinlə, yeni cümlə quruluşu ilə yaz.\n"
    "3. Üslub: ciddi, aydın, neytral, qısa. Emosional, şişirdilmiş və klikbeyt ifadələr olmasın.\n"
    "4. Mənbədəki 'Bakı, 17 noyabr, AZƏRTAC' kimi başlanğıc qeydlərini və agentlik adlarını yazma.\n"
    "5. headline: ən çox 14 söz, 100 simvoldan çox olmasın, məlumat verən tam fikir olsun, sonda nöqtə qoyma.\n"
    "6. summary: 2-3 qısa cümlə, cəmi 300 simvoldan çox olmasın.\n"
    "7. category: bu siyahıdan yalnız birini seç: " + ", ".join(CATEGORIES) + ". "
    "Hava proqnozu CƏMİYYƏT sayılır.\n"
    "8. highlight: headline-dakı 1-3 əsas sözü (ad, rəqəm, açar söz) headline-da necə yazılıbsa, "
    "eyni cür yaz.\n"
    "9. Mətn xəbər deyilsə (reklam, elan, kifayət qədər məlumat yoxdur), skip: true qaytar.\n"
    "10. Azərbaycan ədəbi dilinin qrammatikasına diqqət et: felləri, hal şəkilçilərini və cümlə "
    "quruluşunu düzgün işlət. Yazdıqdan sonra mətni bir daha oxuyub səhvləri düzəlt.\n"
    "11. İDDİA VƏ İTTİHAMLAR: mənbədə bir tərəfin iddiası, ittihamı, ittiham aktı, şübhəsi və ya "
    "ifadəsi varsa, onu sübut olunmuş fakt kimi yazma. Aid etməni mütləq saxla "
    "('ittihama görə', 'iddiaya görə', 'bildirilib'). Heç kimi təqsirli adlandırma.\n"
    "12. Cinayət, qəza, məhkəmə, müharibə və toqquşma xəbərlərində adi şəxslərin (şübhəli, "
    "zərərçəkmiş, şahid, uşaq) adlarını yazma. Yalnız vəzifəli rəsmi şəxslərin və tanınmış "
    "ictimai şəxslərin adlarını yaz.\n"
    "13. KİM DEDİ: rəsmi şəxsin, qurumun və ya təşkilatın fikri, qiymətləndirməsi, proqnozu və ya vədi "
    "varsa, onu fakt kimi yazma, kimin dediyini göstər və başlıqda da saxla. Məsələn, 'Almaniya "
    "əlaqələri yeni təməl üzərində qurur' yox, 'Almaniyanın xarici işlər naziri əlaqələrin yeni "
    "təməl üzərində qurulduğunu bildirib'. Mənbədə olmayan qiymətləndirmə əlavə etmə.\n"
    "14. sensitive: cinayət, qəza, ölüm, məhkəmə, silahlı toqquşma, ordu, sağlamlıq iddiaları və ya "
    "səhv məlumatın kiməsə zərər verə biləcəyi mövzularda true, qalan hallarda false yaz.\n\n"
    'Cavabı yalnız JSON formatında ver: {"skip": false, "category": "...", '
    '"headline": "...", "summary": "...", "highlight": ["..."], "sensitive": false}'
)
# -----------------------------------------


def recent_headlines(hours=72):
    """Son 72 saatda hazırlanmış başlıqlar (təkrarları günlər arası da tutmaq üçün)."""
    f = Path("pending.json")
    if not f.exists():
        return []
    try:
        items = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for p in items:
        try:
            if now - datetime.fromisoformat(p["created"]) <= timedelta(hours=hours):
                out.append(p["headline"])
        except Exception:
            continue
    return out[-40:]


def fetch_article_text(url):
    """Xəbər səhifəsini açıb əsas mətni çıxarır (yalnız AI-yə anlamaq üçün veriləcək)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            charset = r.headers.get_content_charset() or "utf-8"
        soup = BeautifulSoup(raw.decode(charset, errors="replace"), "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        root = soup.find("article") or soup
        paragraphs = [p.get_text(" ", strip=True) for p in root.find_all("p")]
        paragraphs = [p for p in paragraphs if len(p) > 40]
        return " ".join(paragraphs)[:4000]
    except Exception as e:
        print("  Səhifə oxunmadı:", str(e)[:100])
        return ""


def parse_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    return json.loads(raw)


def ask_gemini(item, text, previous):
    prompt = PROMPT
    if previous:
        prompt += (
            "\n\nARTIQ HAZIRLANMIŞ XƏBƏRLƏRİN BAŞLIQLARI:\n- "
            + "\n- ".join(previous)
            + "\nƏgər bu xəbər yuxarıdakılardan biri ilə eyni hadisədən və ya eyni mövzudan "
            "bəhs edirsə, skip: true qaytar."
        )
    prompt += (
        "\n\nMƏNBƏ: " + item["source"]
        + "\nBAŞLIQ: " + item["title"]
        + "\nMƏTN:\n" + text
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    url = "https://generativelanguage.googleapis.com/v1beta/models/" + MODEL + ":generateContent"
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            return parse_json(raw)
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
            if e.code in (429, 500, 503) and attempt < 2:
                print("  Gemini məşğuldur (HTTP %s), gözləyirəm..." % e.code)
                time.sleep(20 * (attempt + 1))
                continue
            print("  Gemini xətası HTTP", e.code, msg)
            return None
        except Exception as e:
            print("  Xəta:", str(e)[:150])
            if attempt < 2:
                time.sleep(10)
                continue
            return None
    return None


def send_telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        data = urllib.parse.urlencode(
            {"chat_id": TG_CHAT, "text": text[:4000], "disable_web_page_preview": "true"}
        ).encode()
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage", data=data
        )
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print("  Telegram xətası:", str(e)[:100])


def main():
    if not API_KEY:
        print("GEMINI_API_KEY tapılmadı. GitHub Secrets-i yoxlayın.")
        raise SystemExit(1)
    if not IN_FILE.exists():
        print("selected_news.json yoxdur.")
        raise SystemExit(1)

    items = json.loads(IN_FILE.read_text(encoding="utf-8"))
    drafts = []
    previous = recent_headlines()  # artıq hazırlanmış başlıqlar (təkrarları tutmaq üçün)

    for i, item in enumerate(items, 1):
        print("%d/%d [%s] %s" % (i, len(items), item["source"], item["title"]))

        text = item.get("snippet", "")
        if item.get("needs_fulltext"):
            full = fetch_article_text(item["link"])
            if len(full) > len(text):
                text = full
        if len(text) < MIN_TEXT:
            print("  Mətn çox qısadır, buraxılır.")
            continue

        res = ask_gemini(item, text, previous)
        time.sleep(PAUSE_SECONDS)
        if not res or res.get("skip"):
            print("  AI bu xəbəri buraxdı (reklam, məlumat azlığı və ya təkrar).")
            continue

        headline = str(res.get("headline", "")).strip().rstrip(".")
        summary = str(res.get("summary", "")).strip()
        category = str(res.get("category", "")).strip().upper()
        if category not in CATEGORIES:
            category = "CƏMİYYƏT"
        highlight = [
            w for w in (res.get("highlight") or [])
            if isinstance(w, str) and w and w in headline
        ][:3]
        if not headline or not summary:
            print("  Boş cavab, buraxılır.")
            continue

        sensitive = bool(res.get("sensitive"))
        draft = {
            "category": category,
            "headline": headline,
            "summary": summary,
            "highlight": highlight,
            "source_note": "Mənbə: " + item["source"],
            "link": item["link"],
            "sensitive": sensitive,
        }
        drafts.append(draft)
        previous.append(headline)

        warning = "⚠️ HƏSSAS MÖVZU: paylaşmazdan əvvəl mənbə ilə diqqətlə yoxla\n\n" if sensitive else ""
        send_telegram(
            "%s[%s]\n%s\n\n%s\n\n%s\n%s"
            % (warning, category, headline, summary, draft["source_note"], item["link"])
        )

    OUT_FILE.write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nHazır mətnlər: %d / %d" % (len(drafts), len(items)))


if __name__ == "__main__":
    main()
