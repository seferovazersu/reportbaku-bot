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
from PIL import Image, ImageChops, ImageDraw

from make_post import (MARGIN, NAVY, ELECTRIC, RED, cover,
                       font, wrap_words, fit_block, highlight_indices, draw_lines,
                       draw_tag, draw_topbar, draw_footer, background, gradient_mask, darken,
                       az_upper, baku_today)

VW, VH = 1080, 1920
FPS = 24
TOP_H = 750                     # yuxarıdakı foto/fon hissəsi
FOOTER_Y = TOP_H + 722          # mənbə sətri (Instagram-ın alt yazılarından yuxarıda qalır)
LEAD, TAIL = 0.6, 1.6           # səsdən əvvəl və sonra sakit anlar (san.)
MAX_CHUNK_WORDS = 6             # bir altyazı parçasında ən çox söz
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


def make_video(category, headline, summary, source_note, out, photo=None,
               highlight=None, date=None, voiceover=None, inset=None):
    """Videonu yaradır. {'duration':..., 'model':..., 'words':...} qaytarır, alınmasa None."""
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
    print("  Səs: %.1f san. (%s), video: %.1f san., %d söz" % (speech, model, total, len(script.split())))

    # --- Statik elementlər ---
    base = Image.new("RGB", (VW, VH), NAVY)
    bd = ImageDraw.Draw(base)
    bd.rectangle([0, TOP_H, VW, TOP_H + 8], fill=ELECTRIC)
    bd.rectangle([0, TOP_H, 230, TOP_H + 8], fill=RED)

    bg = background(photo, (VW, TOP_H), base_y=TOP_H - 4)
    bg = darken(bg, gradient_mask((VW, TOP_H), 0, 260, 0.65, 0.0))

    topbar = Image.new("RGBA", (VW, 160), (0, 0, 0, 0))
    draw_topbar(topbar, date)
    base.paste(topbar, (0, 120), topbar)

    footer = Image.new("RGBA", (VW, 96), (0, 0, 0, 0))
    draw_footer(footer, 0, source_note)
    base.paste(footer, (0, FOOTER_Y), footer)

    # --- Kiçik pəncərə (stok video/foto) ---
    inset_mask = Image.new("L", (IW, IH), 0)
    ImageDraw.Draw(inset_mask).rounded_rectangle([0, 0, IW - 1, IH - 1], radius=28, fill=255)
    reader, inset_photo, last_inset, label = None, None, None, None
    if inset:
        try:
            if inset.get("kind") == "video":
                reader = subprocess.Popen(
                    ["ffmpeg", "-loglevel", "error", "-stream_loop", "-1", "-i", inset["path"],
                     "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,fps=%d" % (IW, IH, IW, IH, FPS),
                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            else:
                inset_photo = cover(Image.open(inset["path"]).convert("RGB"), (IW, IH))
            lf = font(18, "bold")
            lw = int(ImageDraw.Draw(Image.new("RGB", (10, 10))).textlength("İLLÜSTRATİV GÖRÜNTÜ", font=lf)) + 44
            label = Image.new("RGBA", (lw, 38), (0, 0, 0, 0))
            ld = ImageDraw.Draw(label)
            ld.rounded_rectangle([0, 0, lw - 1, 37], radius=19, fill=(4, 12, 30, 190))
            ld.text((22, 8), "İLLÜSTRATİV GÖRÜNTÜ", font=lf, fill=(220, 228, 244))
        except Exception as e:
            print("  Pəncərə hazırlanmadı:", str(e)[:100])
            reader, inset_photo = None, None

    # --- Başlıq ---
    max_w = VW - 2 * MARGIN
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    h_words = headline.split()
    hf, hsize, h_lines, _ = fit_block(probe, h_words, "extrabold", max_w, 76, 48, 5, 1.12, max_h=330)
    hl = highlight_indices(h_words, highlight)
    tag_y = TOP_H + 52
    head_y = tag_y + 48 + 22
    head_h = len(h_lines) * int(hsize * 1.12)

    layers = []  # (qat, x, y, görünmə_vaxtı)
    tag_layer = Image.new("RGBA", (700, 52), (0, 0, 0, 0))
    draw_tag(tag_layer, 0, 0, az_upper(category))
    layers.append((tag_layer, MARGIN, tag_y, 0.15))
    for li, line in enumerate(h_lines):
        lay = line_layer(h_words, line, hf, hsize, 1.12,
                         lambda i: ELECTRIC if i in hl else (255, 255, 255), max_w)
        layers.append((lay, MARGIN, head_y + li * int(hsize * 1.12), 0.3 + 0.22 * li))

    # --- Altyazı parçaları ---
    cap_y = head_y + head_h + 44
    cap_h = FOOTER_Y - 40 - cap_y
    chunks = split_chunks(script)
    size = pick_caption_size(probe, chunks, max_w, cap_h)
    cf = font(size, "semibold")
    chunk_layers = []
    for c in chunks:
        cw = c.split()
        chunk_layers.append(block_layer(cw, wrap_words(probe, cw, cf, max_w), cf, size, 1.25,
                                        (255, 255, 255), max_w))

    weights = [chunk_weight(c) for c in chunks]
    wsum = float(sum(weights)) or 1.0
    starts, acc = [], 0.0
    for w in weights:
        starts.append(LEAD + speech * acc / wsum)
        acc += w

    # --- Kadrları yaz ---
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    audio_path = str(Path(out).with_suffix(".pcm"))
    pad_head = b"\x00\x00" * int(rate * LEAD)
    pad_tail = b"\x00\x00" * int(rate * TAIL)
    Path(audio_path).write_bytes(pad_head + pcm + pad_tail)

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (VW, VH), "-r", str(FPS), "-i", "-",
           "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", audio_path,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
           "-movflags", "+faststart", "-shortest", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    bar_y = FOOTER_Y - 18
    bar_w = VW - 2 * MARGIN
    for i in range(n_frames):
        t = i / FPS
        frame = base.copy()
        z = 1 + 0.07 * (t / total)                       # yavaş yaxınlaşma (zoom)
        cw_, ch_ = VW / z, TOP_H / z
        x0, y0 = (VW - cw_) / 2, (TOP_H - ch_) / 2
        top = bg.crop((int(x0), int(y0), int(x0 + cw_), int(y0 + ch_))).resize((VW, TOP_H), Image.BILINEAR)
        frame.paste(top, (0, 0))
        frame.paste(topbar, (0, 120), topbar)
        piece = None
        if reader is not None:
            raw = read_exact(reader.stdout, IW * IH * 3)
            if len(raw) == IW * IH * 3:
                last_inset = Image.frombytes("RGB", (IW, IH), raw)
            elif last_inset is None:
                reader.kill()
                reader = None
            piece = last_inset
        elif inset_photo is not None:
            zz = 1 + 0.06 * (t / total)
            pw, ph = IW / zz, IH / zz
            px0, py0 = (IW - pw) / 2, (IH - ph) / 2
            piece = inset_photo.crop((int(px0), int(py0), int(px0 + pw), int(py0 + ph))).resize((IW, IH), Image.BILINEAR)
        if piece is not None:
            frame.paste(piece, (IX, IY), inset_mask)
            ImageDraw.Draw(frame).rounded_rectangle([IX - 3, IY - 3, IX + IW + 2, IY + IH + 2],
                                                    radius=30, outline=ELECTRIC, width=3)
            frame.paste(label, (IX + 22, IY + IH - 58), label)
        for lay, x, y, ta in layers:
            paste_fade(frame, lay, x, y, clamp((t - ta) / 0.35))
        if t >= starts[0] - 0.05:                         # aktiv altyazı parçası
            idx = 0
            for k, st in enumerate(starts):
                if t >= st - 0.05:
                    idx = k
            paste_fade(frame, chunk_layers[idx], MARGIN, cap_y,
                       clamp((t - starts[idx] + 0.05) / 0.12), rise=10)
        d = ImageDraw.Draw(frame)                         # irəliləyiş xətti
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
