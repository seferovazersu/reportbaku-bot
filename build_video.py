"""
ReportBaku - Reels üçün video generatoru (1080x1920, 24 kadr/san).

Hər xəbər üçün:
  - Gemini TTS ilə Azərbaycanca diktor səsi (mətnin uzunluğu xəbərə görə dəyişir),
  - brend fonu (Bakı siluet), sabit başlıq,
  - danışılan sözlər qısa altyazı parçaları kimi səsin tempinə uyğun görünür.

İstifadə:
    from build_video import make_video
    make_video(category, headline, summary, source_note, out,
               photo=None, highlight=[...], voiceover="...")

Sınaq (Telegram-a göndərir):
    python build_video.py --demo
"""

import base64
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from make_post import (MARGIN, NAVY, NAVY_DEEP, ELECTRIC, RED, cover,
                       font, wrap_words, fit_block, highlight_indices, draw_lines,
                       draw_tag, draw_topbar, draw_footer, background, gradient_mask, darken,
                       az_upper, baku_today)

VW, VH = 1080, 1920
FPS = 24
TOP_H = 750                     # yuxarıdakı foto/fon hissəsi
FOOTER_Y = TOP_H + 722          # mənbə sətri (Instagram-ın alt yazılarından yuxarıda qalır)
LEAD, TAIL = 0.6, 1.6           # səsdən əvvəl və sonra sakit anlar (san.)
MAX_CHUNK_WORDS = 6
FULL_TOP = 1060                 # media olduqda yuxarı pəncərənin hündürlüyü (tam eni)
FULL_FOOTER = 1450              # media olduqda alt zolağın yeri
IW, IH, IY = 920, 430, 290      # kiçik pəncərənin (stok görüntü) ölçüsü və yuxarıdan məsafəsi
IX = (VW - IW) // 2
TTS_MODELS = [m.strip() for m in os.getenv(
    "TTS_MODELS",
    "gemini-3.1-flash-tts-preview,gemini-3.8-flash-lite-tts,gemini-3.8-flash-tts").split(",") if m.strip()]
TTS_VOICE = os.getenv("TTS_VOICE", "Kore")
API_KEY = os.environ.get("GEMINI_API_KEY", "")
TTS_PROMPT = ("Read the following Azerbaijani text aloud in a calm, serious news-anchor voice, "
              "with clear and natural Azerbaijani pronunciation:\n")


# ---------------- Abreviaturalar ----------------
# Səsləndirmədən əvvəl tək qalan abreviaturalar tam adı ilə əvəz olunur (şəkilçili formaları
# AI özü tam yazır; bu lüğət ehtiyat üçündür). Yeni abreviatur əlavə etmək üçün bura sətir əlavə edin.
ABBREVIATIONS = {
    "FHN": "Fövqəladə Hallar Nazirliyi",
    "XİN": "Xarici İşlər Nazirliyi",
    "DİN": "Daxili İşlər Nazirliyi",
    "MN": "Müdafiə Nazirliyi",
    "ƏƏSMN": "Əmək və Əhalinin Sosial Müdafiəsi Nazirliyi",
    "DSX": "Dövlət Sərhəd Xidməti",
    "DTX": "Dövlət Təhlükəsizlik Xidməti",
    "DMX": "Dövlət Miqrasiya Xidməti",
    "DYP": "Dövlət Yol Polisi",
    "AYNA": "Azərbaycan Yerüstü Nəqliyyat Agentliyi",
    "BNA": "Bakı Nəqliyyat Agentliyi",
    "ADY": "Azərbaycan Dəmir Yolları",
    "DSMF": "Dövlət Sosial Müdafiə Fondu",
    "DİM": "Dövlət İmtahan Mərkəzi",
    "AMEA": "Azərbaycan Milli Elmlər Akademiyası",
    "ƏDV": "əlavə dəyər vergisi",
    "BMT": "Birləşmiş Millətlər Təşkilatı",
    "ATƏT": "Avropa Təhlükəsizlik və Əməkdaşlıq Təşkilatı",
    "MDB": "Müstəqil Dövlətlər Birliyi",
    "ABŞ": "Amerika Birləşmiş Ştatları",
    "BƏƏ": "Birləşmiş Ərəb Əmirlikləri",
    "Aİ": "Avropa İttifaqı",
}


def expand_abbreviations(text):
    """Tək qalan abreviaturaları (məs. 'FHN') tam adı ilə əvəz edir."""
    for abbr in sorted(ABBREVIATIONS, key=len, reverse=True):
        def repl(m, full=ABBREVIATIONS[abbr]):
            before = m.string[:m.start()].rstrip()
            if (not before or before[-1] in ".!?…") and full[:1].islower():
                return full[:1].upper() + full[1:]      # cümlə başında böyük hərf
            return full
        text = re.sub(r"(?<![\w-])" + re.escape(abbr) + r"(?![\w-])", repl, text)
    return text


# ---------------- Səs ----------------
def tts_pcm(text):
    """Mətni səsləndirir. (pcm_bayt, nümunə_tezliyi, model) qaytarır, alınmasa None."""
    if not API_KEY:
        print("  GEMINI_API_KEY yoxdur.")
        return None
    for model in TTS_MODELS:
        for attempt in range(2):
            try:
                body = {
                    "contents": [{"parts": [{"text": TTS_PROMPT + text}]}],
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": TTS_VOICE}}},
                    },
                }
                r = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model,
                    json=body, headers={"x-goog-api-key": API_KEY}, timeout=150)
                if r.status_code in (429, 500, 503) and attempt == 0:
                    print("  %s məşğuldur (HTTP %s), gözləyirəm..." % (model, r.status_code))
                    time.sleep(20)
                    continue
                if not r.ok:
                    print("  %s: HTTP %s %s" % (model, r.status_code, r.text[:120].replace("\n", " ")))
                    break
                part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                pcm = base64.b64decode(part["data"])
                m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
                return pcm, int(m.group(1)) if m else 24000, model
            except Exception as e:
                print("  %s: xəta %s" % (model, type(e).__name__))
                break
    return None


# ---------------- Altyazı parçaları ----------------
def split_chunks(text, max_words=MAX_CHUNK_WORDS):
    """Mətni qısa altyazı parçalarına bölür (cümlə və vergül sərhədlərinə uyğun)."""
    chunks = []
    for sentence in re.split(r"(?<=[.!?…])\s+", text.strip()):
        cur = []
        for w in sentence.split():
            cur.append(w)
            end_clause = w.endswith((",", ";", ":", "—", "–")) and len(cur) >= 3
            if len(cur) >= max_words or end_clause:
                chunks.append(cur)
                cur = []
        if cur:
            if chunks and len(cur) == 1 and not chunks[-1][-1].endswith((".", "!", "?", "…")):
                chunks[-1].extend(cur)       # tək qalan sözü əvvəlki parçaya qat
            else:
                chunks.append(cur)
    return [" ".join(c) for c in chunks if c]


def chunk_weight(chunk):
    """Parçanın səsləndirmə müddətinə təxmini nisbət (simvol sayı + durğu fasiləsi)."""
    w = len(chunk) + 1
    if chunk.endswith((".", "!", "?", "…")):
        w += 7
    elif chunk.endswith((",", ";", ":")):
        w += 3
    return w


# ---------------- Yardımçılar ----------------
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def line_layer(words, line, f, size, line_h, color_fn, width):
    """Bir sətir üçün şəffaf qat."""
    step = int(size * line_h)
    layer = Image.new("RGBA", (width, step + 6), (0, 0, 0, 0))
    draw_lines(ImageDraw.Draw(layer), 0, 0, words, [line], f, size, line_h, color_fn)
    return layer


def block_layer(words, lines, f, size, line_h, color, width):
    """Bir neçə sətirdən ibarət şəffaf qat."""
    step = int(size * line_h)
    layer = Image.new("RGBA", (width, step * len(lines) + 8), (0, 0, 0, 0))
    draw_lines(ImageDraw.Draw(layer), 0, 0, words, lines, f, size, line_h, lambda i: color)
    return layer


def paste_fade(frame, layer, x, y, alpha, rise=22):
    """Qatı alpha dərəcəsi ilə yapışdırır (yuxarı sürüşərək görünür)."""
    if alpha <= 0:
        return
    yy = y + int((1 - alpha) * rise)
    if alpha >= 0.999:
        frame.paste(layer, (x, yy), layer)
        return
    a = layer.getchannel("A")
    a = ImageChops.multiply(a, Image.new("L", a.size, int(255 * alpha)))
    frame.paste(layer, (x, yy), a)


def fit_blur(img, size):
    """Şəkli kəsmədən sığdırır; qalan yer onun bulanıq surəti ilə dolur."""
    w, h = size
    bgc = cover(img.convert("RGB"), size).filter(ImageFilter.GaussianBlur(26))
    bgc = Image.blend(bgc, Image.new("RGB", size, (4, 12, 30)), 0.35)
    fg = img.convert("RGB")
    fg.thumbnail(size, Image.LANCZOS)
    bgc.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bgc


def read_exact(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def pick_caption_size(probe, chunks, max_w, cap_h):
    """Bütün parçalar ≤3 sətirə və verilmiş hündürlüyə sığan ən böyük şrift ölçüsü."""
    for size in range(54, 33, -2):
        f = font(size, "semibold")
        most = max(len(wrap_words(probe, c.split(), f, max_w)) for c in chunks)
        if most <= 3 and most * int(size * 1.25) <= cap_h:
            return size
    return 34


def make_cover_image(photo, category, headline, out, highlight=None, date=None):
    """Reels örtüyü (1080x1920): xəbərin şəkli tam ekran, altda tag və böyük başlıq.
    Profil şəbəkəsində ortadakı 3:4 hissə görünür, ona görə mətn y=950-1500 aralığındadır."""
    date = date or baku_today()
    img = background(photo, (VW, VH), base_y=VH - 4)
    mask = ImageChops.lighter(gradient_mask((VW, VH), 700, 1500, 0.0, 0.93),
                              gradient_mask((VW, VH), 0, 520, 0.55, 0.0))
    img = darken(img, mask)
    top = Image.new("RGBA", (VW, 160), (0, 0, 0, 0))
    draw_topbar(top, date)
    img.paste(top, (0, 200), top)
    d = ImageDraw.Draw(img)
    max_w = VW - 2 * MARGIN
    words = headline.split()
    f, size, lines, _ = fit_block(d, words, "extrabold", max_w, 92, 54, 5, 1.1, max_h=470)
    hl = highlight_indices(words, highlight)
    h = len(lines) * int(size * 1.1)
    y = 1520 - h
    draw_tag(img, MARGIN, y - 48 - 22, az_upper(category))
    draw_lines(d, MARGIN, y, words, lines, f, size, 1.1,
               lambda i: ELECTRIC if i in hl else (255, 255, 255))
    foot = Image.new("RGBA", (VW, 96), (0, 0, 0, 0))
    draw_footer(foot, 0, "")
    img.paste(foot, (0, 1570), foot)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=93)
    return out


def make_video(category, headline, summary, source_note, out, photo=None,
               highlight=None, date=None, voiceover=None, inset=None):
    """Videonu yaradır (yalnız diktor səsi, altyazı yoxdur). {'duration':..., 'model':..., 'words':...} və ya None."""
    headline, summary = headline.strip(), (summary or "").strip()
    script = (voiceover or "").strip() or (headline.rstrip(".") + ". " + summary)
    script = expand_abbreviations(script)
    got = tts_pcm(script)
    if not got:
        return None
    pcm, rate, model = got
    speech = len(pcm) / 2 / rate
    total = LEAD + speech + TAIL
    n_frames = int(total * FPS)
    date = date or baku_today()
    top_h = FULL_TOP if inset else TOP_H
    footer_y = FULL_FOOTER if inset else FOOTER_Y
    print("  Səs: %.1f san. (%s), video: %.1f san., %d söz" % (speech, model, total, len(script.split())))

    # --- Yuxarı hissə ---
    reader, piece_img, last_inset = None, None, None
    if inset:
        try:
            if inset.get("kind") == "video":
                flt = ("[0:v]split[a][b];[a]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,boxblur=25:3[bg];"
                       "[b]scale=%d:%d:force_original_aspect_ratio=decrease[fg];"
                       "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=%d,format=rgb24" % (VW, top_h, VW, top_h, VW, top_h, FPS))
                reader = subprocess.Popen(
                    ["ffmpeg", "-loglevel", "error", "-stream_loop", "-1", "-i", inset["path"],
                     "-filter_complex", flt, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            else:
                piece_img = fit_blur(Image.open(inset["path"]), (VW, top_h))
        except Exception as e:
            print("  Media hazırlanmadı:", str(e)[:100])
            reader, piece_img = None, None
    use_media = reader is not None or piece_img is not None
    if not use_media:
        top_h, footer_y = TOP_H, FOOTER_Y
    bg = None
    if not use_media:
        bg = background(photo, (VW, top_h), base_y=top_h - 4)
        bg = darken(bg, gradient_mask((VW, top_h), 0, 260, 0.65, 0.0))

    base = Image.new("RGB", (VW, VH), NAVY)
    bd = ImageDraw.Draw(base)
    bd.rectangle([0, top_h, VW, top_h + 8], fill=ELECTRIC)
    bd.rectangle([0, top_h, 230, top_h + 8], fill=RED)
    topbar = Image.new("RGBA", (VW, 160), (0, 0, 0, 0))
    draw_topbar(topbar, date)
    footer = Image.new("RGBA", (VW, 96), (0, 0, 0, 0))
    draw_footer(footer, 0, source_note)
    base.paste(footer, (0, footer_y), footer)
    shade = Image.new("RGB", (VW, 340), NAVY_DEEP)
    shade_mask = gradient_mask((VW, 340), 0, 340, 0.7, 0.0)

    # --- Başlıq (kateqoriya etiketi mediyanın alt kənarında) ---
    max_w = VW - 2 * MARGIN
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    h_words = headline.split()
    tag_y = top_h - 26 if use_media else top_h + 52
    head_y = tag_y + 48 + 26
    hf, hsize, h_lines, _ = fit_block(probe, h_words, "extrabold", max_w, 76, 46, 5, 1.12,
                                      max_h=max(150, footer_y - 50 - head_y))
    hl = highlight_indices(h_words, highlight)
    layers = []
    tag_layer = Image.new("RGBA", (700, 52), (0, 0, 0, 0))
    draw_tag(tag_layer, 0, 0, az_upper(category))
    layers.append((tag_layer, MARGIN, tag_y, 0.15))
    for li, line in enumerate(h_lines):
        lay = line_layer(h_words, line, hf, hsize, 1.12,
                         lambda i: ELECTRIC if i in hl else (255, 255, 255), max_w)
        layers.append((lay, MARGIN, head_y + li * int(hsize * 1.12), 0.3 + 0.22 * li))

    # --- Kadrları yaz ---
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    audio_path = str(Path(out).with_suffix(".pcm"))
    Path(audio_path).write_bytes(b"\x00\x00" * int(rate * LEAD) + pcm + b"\x00\x00" * int(rate * TAIL))
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (VW, VH), "-r", str(FPS), "-i", "-",
           "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", audio_path,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
           "-movflags", "+faststart", "-shortest", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    bar_y = footer_y - 18
    bar_w = VW - 2 * MARGIN
    for i in range(n_frames):
        t = i / FPS
        frame = base.copy()
        if reader is not None:
            raw = read_exact(reader.stdout, VW * top_h * 3)
            if len(raw) == VW * top_h * 3:
                last_inset = Image.frombytes("RGB", (VW, top_h), raw)
            top = last_inset if last_inset is not None else Image.new("RGB", (VW, top_h), NAVY)
        else:
            src = piece_img if piece_img is not None else bg
            z = 1 + 0.05 * (t / total)                     # yavaş yaxınlaşma (zoom)
            cw_, ch_ = VW / z, top_h / z
            x0, y0 = (VW - cw_) / 2, (top_h - ch_) / 2
            top = src.crop((int(x0), int(y0), int(x0 + cw_), int(y0 + ch_))).resize((VW, top_h), Image.BILINEAR)
        frame.paste(top, (0, 0))
        if use_media:
            frame.paste(shade, (0, 0), shade_mask)         # üst loqonun oxunması üçün kölgə
        frame.paste(topbar, (0, 120), topbar)
        for lay, x, y, ta in layers:
            paste_fade(frame, lay, x, y, clamp((t - ta) / 0.35))
        d = ImageDraw.Draw(frame)                          # irəliləyiş xətti
        d.rectangle([MARGIN, bar_y, MARGIN + bar_w, bar_y + 5], fill=(30, 46, 80))
        d.rectangle([MARGIN, bar_y, MARGIN + int(bar_w * clamp(t / total)), bar_y + 5], fill=ELECTRIC)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    if reader is not None:
        reader.kill()
    try:
        os.remove(audio_path)
    except OSError:
        pass
    if proc.returncode != 0:
        print("  ffmpeg xətası")
        return None
    return {"duration": total, "model": model, "words": len(script.split())}


# ---------------- Sınaq ----------------
def send_video(path, caption):
    token, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        print("Telegram ayarları yoxdur.")
        return
    with open(path, "rb") as f:
        r = requests.post("https://api.telegram.org/bot%s/sendVideo" % token,
                          data={"chat_id": chat, "caption": caption[:900], "supports_streaming": "true",
                                "width": VW, "height": VH},
                          files={"video": f}, timeout=180)
    print("Telegram:", "göndərildi" if r.ok else r.text[:200])


def demo():
    sample = dict(
        category="SİYASƏT",
        headline="Almaniyanın xarici işlər naziri əlaqələrin yeni təməl üzərində qurulduğunu bildirib",
        summary="Nazir Cənubi Qafqazın dönüş nöqtəsində olduğunu qeyd edib.",
        voiceover="Almaniyanın xarici işlər naziri Azərbaycanla münasibətlərin yeni strateji təməl "
                  "üzərində qurulduğunu bildirib. Nazirin sözlərinə görə, Cənubi Qafqaz dönüş nöqtəsindədir, "
                  "bölgədə sülhə nail olmaq üçün tarixi şans yaranıb. O, ötən il sülh sazişinin "
                  "paraflanmasını əhəmiyyətli mərhələ adlandırıb.",
        highlight=["Almaniyanın"], source_note="Mənbə: AZƏRTAC")
    print("Sınaq videosu:", sample["headline"])
    sample["source_note"] = ""
    try:
        from stock_media import find_clip
        sample["inset"] = find_clip(["city traffic", "city street"], "demo", "stock_tmp", "demo")
        print("Pexels:", sample["inset"] if sample["inset"] else "açar yoxdur və ya nəticə tapılmadı")
    except Exception as e:
        print("Pexels xətası:", str(e)[:100])
    res = make_video(out="video_test.mp4", **sample)
    if not res:
        print("Video alınmadı.")
        sys.exit(1)
    send_video("video_test.mp4", "Sınaq videosu (%.0f san., %d söz, səs: %s)\n%s" % (
        res["duration"], res["words"], res["model"], sample["headline"]))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        print("İstifadə: python build_video.py --demo")
