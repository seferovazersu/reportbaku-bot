"""
ReportBaku - Instagram post şəkli generatoru (1080x1350).

İstifadə:
    from make_post import make_post
    make_post(photo, category, headline, summary, out,
              variant="overlay" | "panel", highlight=["söz", ...], source_note="Mənbə: ...")

photo = foto faylının yolu və ya None (onda markalı Bakı fonu çəkilir).
Skripti birbaşa işə salsanız (python make_post.py), nümunə şəkillər və loqo yaranır.
"""

import datetime
import os
import random
import subprocess
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# ---------------- Marka ----------------
W, H = 1080, 1350
NAVY = (9, 24, 56)            # tünd göy
NAVY_DEEP = (4, 12, 30)
ELECTRIC = (46, 125, 255)     # elektrik mavisi
RED = (229, 38, 45)           # vurğu rəngi
WHITE = (255, 255, 255)
MUTED = (196, 208, 228)
SOFT = (150, 166, 196)
MARGIN = 64

SLOGAN = "HADİSƏLƏRİN DOĞRU ÜZÜ."
AZ_MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun",
             "iyul", "avqust", "sentyabr", "oktyabr", "noyabr", "dekabr"]

# ---------------- Şrift ----------------
FONT_DIRS = [
    "fonts",
    "/usr/share/fonts/opentype/inter",
    "/usr/share/fonts/truetype/inter",
]
FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
WEIGHTS = {"regular": "Regular", "medium": "Medium", "semibold": "SemiBold",
           "bold": "Bold", "extrabold": "ExtraBold"}
_warned = False


@lru_cache(maxsize=None)
def font(size, weight="bold"):
    global _warned
    name = "Inter-" + WEIGHTS[weight]
    for d in FONT_DIRS:
        for ext in (".otf", ".ttf"):
            p = os.path.join(d, name + ext)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "Inter:" + weight],
                             capture_output=True, text=True).stdout.strip()
        if out and "nter" in out and os.path.exists(out):
            return ImageFont.truetype(out, size)
    except Exception:
        pass
    if not _warned:
        print("DİQQƏT: Inter şrifti tapılmadı, ehtiyat şrift işlədilir.")
        _warned = True
    for p in FALLBACKS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ---------------- Azərbaycan hərfləri ----------------
def az_upper(s):
    return s.replace("i", "İ").replace("ı", "I").upper()


def az_lower(s):
    return s.replace("İ", "i").replace("I", "ı").lower()


def norm_word(w):
    return az_lower(w).strip(".,:;!?\"'“”«»()–-…")


# ---------------- Loqo ----------------
def draw_logo(size, style="light"):
    """RB loqosu. style='light': ağ kvadrat, tünd hərflər. style='dark': tünd kvadrat, ağ hərflər."""
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg, fg = (WHITE, NAVY) if style == "light" else (NAVY, WHITE)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=bg + (255,))
    f = font(int(S * 0.50), "extrabold")
    bbox = d.textbbox((0, 0), "RB", font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (S - tw) / 2 - bbox[0]
    y = (S - th) / 2 - bbox[1] - S * 0.07
    d.text((x, y), "RB", font=f, fill=fg)
    bar_h = int(S * 0.06)
    bar_w = int(tw * 0.46)
    bx = int((S - tw) / 2)
    by = int(S * 0.76)
    d.rounded_rectangle([bx, by, bx + bar_w, by + bar_h], radius=bar_h // 2, fill=RED)
    return img.resize((size, size), Image.LANCZOS)


def make_profile(out, size=1080):
    """Instagram profil şəkli (dairəvi kəsiləcək, loqo mərkəzdədir)."""
    img = vertical_gradient((size, size), (14, 38, 90), (4, 12, 30))
    glow = Image.new("L", (size, size), 0)
    ImageDraw.Draw(glow).ellipse([size * 0.35, -size * 0.2, size * 1.2, size * 0.6], fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.12))
    img = Image.composite(Image.new("RGB", (size, size), ELECTRIC), img, glow)
    d = ImageDraw.Draw(img)
    c = size // 2
    r = int(size * 0.40)
    d.ellipse([c - r, c - r, c + r, c + r], outline=ELECTRIC, width=int(size * 0.012))
    logo = draw_logo(int(size * 0.46), "light")
    img.paste(logo, (c - logo.width // 2, c - logo.height // 2), logo)
    img.save(out)


# ---------------- Fon ----------------
def vertical_gradient(size, top, bottom):
    w, h = size
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return col.resize((w, h))


def _flame(d, cx, base, h, hw, lean, fill):
    pts_l, pts_r = [], []
    n = 48
    for i in range(n + 1):
        t = i / n
        half = hw * (1 - t ** 1.7) ** 0.85
        shift = lean * hw * (t ** 2)
        y = base - h * t
        pts_l.append((cx - half + shift, y))
        pts_r.append((cx + half + shift, y))
    d.polygon(pts_l + pts_r[::-1], fill=fill)


def branded_background(size, base_y):
    """Foto olmayanda: tünd göy fon, mavi parıltı və Bakı siluet (Alov Qüllələri)."""
    w, h = size
    img = vertical_gradient((w, h), (12, 34, 82), (4, 12, 30))
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse([w * 0.35, -h * 0.15, w * 1.25, h * 0.35], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img = Image.composite(Image.new("RGB", (w, h), ELECTRIC), img, glow)

    sky = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(sky)
    rnd = random.Random(11)
    x = -20
    while x < w:
        bw = rnd.randint(50, 110)
        bh = rnd.randint(70, 230)
        d.rectangle([x, base_y - bh, x + bw, base_y + 4], fill=(15, 40, 96, 255))
        for wy in range(base_y - bh + 14, base_y - 10, 26):
            for wx in range(x + 10, x + bw - 10, 22):
                if rnd.random() < 0.28:
                    d.rectangle([wx, wy, wx + 6, wy + 9], fill=(70, 120, 215, 150))
        x += bw + rnd.randint(2, 14)
    for cx, th_, hw, lean in [(700, 400, 56, 0.55), (800, 350, 52, 0.55), (895, 300, 48, 0.55)]:
        _flame(d, cx, base_y + 4, th_, hw, lean, (22, 60, 140, 255))
        _flame(d, cx + 12, base_y + 4, th_ * 0.93, hw * 0.45, lean, (46, 125, 255, 90))
    d.rectangle([0, base_y, w, h], fill=(7, 20, 48, 255))
    img = img.convert("RGBA")
    img.alpha_composite(sky)
    return img.convert("RGB")


def cover(img, size):
    tw, th = size
    w, h = img.size
    scale = max(tw / w, th / h)
    img = img.resize((int(w * scale) + 1, int(h * scale) + 1), Image.LANCZOS)
    left = (img.width - tw) // 2
    top = (img.height - th) // 2
    return img.crop((left, top, left + tw, top + th))


def background(photo, size, base_y):
    if photo and os.path.exists(photo):
        img = cover(Image.open(photo).convert("RGB"), size)
        return Image.blend(img, Image.new("RGB", size, NAVY), 0.22)  # marka tonu
    return branded_background(size, base_y)


def gradient_mask(size, y0, y1, a0, a1):
    w, h = size
    col = Image.new("L", (1, h))
    px = col.load()
    for y in range(h):
        if y <= y0:
            a = a0
        elif y >= y1:
            a = a1
        else:
            t = (y - y0) / (y1 - y0)
            t = t * t * (3 - 2 * t)
            a = a0 + (a1 - a0) * t
        px[0, y] = int(max(0, min(1, a)) * 255)
    return col.resize((w, h))


def darken(img, mask, color=NAVY_DEEP):
    return Image.composite(Image.new("RGB", img.size, color), img, mask)


# ---------------- Mətn ----------------
def tracked_width(d, text, f, tracking):
    return sum(d.textlength(ch, font=f) + tracking for ch in text) - tracking


def draw_tracked(d, x, y, text, f, fill, tracking):
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + tracking
    return x


def wrap_words(d, words, f, max_w):
    lines, cur, cur_w = [], [], 0
    space = d.textlength(" ", font=f)
    for i, w in enumerate(words):
        ww = d.textlength(w, font=f)
        if cur and cur_w + space + ww > max_w:
            lines.append(cur)
            cur, cur_w = [i], ww
        else:
            cur_w = cur_w + space + ww if cur else ww
            cur.append(i)
    if cur:
        lines.append(cur)
    return lines


def fit_block(d, words, weight, max_w, size_max, size_min, max_lines, line_h, max_h=None):
    """Ölçünü azaldaraq mətni sığdırır. (şrift, ölçü, sətirlər, kəsildi_mi) qaytarır."""
    for size in range(size_max, size_min - 1, -2):
        f = font(size, weight)
        lines = wrap_words(d, words, f, max_w)
        h = len(lines) * int(size * line_h)
        if len(lines) <= max_lines and (max_h is None or h <= max_h):
            return f, size, lines, False
    f = font(size_min, weight)
    lines = wrap_words(d, words, f, max_w)
    limit = max_lines
    if max_h is not None:
        limit = min(limit, max(1, int(max_h // int(size_min * line_h))))
    return f, size_min, lines[:limit], len(lines) > limit


def highlight_indices(words, phrases):
    norm = [norm_word(w) for w in words]
    idx = set()
    for ph in phrases or []:
        pw = [norm_word(x) for x in ph.split()]
        if not pw:
            continue
        for i in range(len(norm) - len(pw) + 1):
            if norm[i:i + len(pw)] == pw:
                idx.update(range(i, i + len(pw)))
    return idx


def draw_lines(d, x, y, words, lines, f, size, line_h, color_fn, truncated=False):
    space = d.textlength(" ", font=f)
    step = int(size * line_h)
    for li, line in enumerate(lines):
        cx = x
        for wi in line:
            txt = words[wi]
            if truncated and li == len(lines) - 1 and wi == line[-1]:
                txt = txt.rstrip(".,;:") + "…"
            d.text((cx, y), txt, font=f, fill=color_fn(wi))
            cx += d.textlength(txt, font=f) + space
        y += step
    return y


def draw_tag(img, x, y, text):
    d = ImageDraw.Draw(img)
    f = font(25, "bold")
    tr = 3
    tw = tracked_width(d, text, f, tr)
    pad = 22
    h = 48
    d.rectangle([x, y, x + tw + 2 * pad, y + h], fill=RED)
    bbox = d.textbbox((0, 0), "A", font=f)
    ty = y + (h - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw_tracked(d, x + pad, ty, text, f, WHITE, tr)
    return h


def az_date(date):
    return "%d %s %d" % (date.day, az_upper(AZ_MONTHS[date.month - 1]), date.year)


def draw_topbar(img, date):
    logo = draw_logo(84, "light")
    img.paste(logo, (MARGIN, 56), logo)
    d = ImageDraw.Draw(img)
    f = font(36, "extrabold")
    bb = d.textbbox((0, 0), "REPORT", font=f)
    ty = 56 + (84 - (bb[3] - bb[1])) / 2 - bb[1]
    x = MARGIN + 84 + 22
    d.text((x, ty), "REPORT", font=f, fill=WHITE)
    d.text((x + d.textlength("REPORT", font=f), ty), "BAKU", font=f, fill=ELECTRIC)
    f2 = font(23, "semibold")
    txt = az_date(date)
    tw = tracked_width(d, txt, f2, 2)
    bb2 = d.textbbox((0, 0), "0", font=f2)
    ty2 = 56 + (84 - (bb2[3] - bb2[1])) / 2 - bb2[1]
    draw_tracked(d, W - MARGIN - tw, ty2, txt, f2, MUTED, 2)


def draw_footer(img, y_line, source_note):
    """Alt zolaq. Mənbə yazısı yoxdursa (şəkil/videoda mənbə göstərilmir), yalnız şüar solda yazılır."""
    d = ImageDraw.Draw(img)
    d.line([(MARGIN, y_line), (W - MARGIN, y_line)], fill=(58, 76, 112), width=2)
    f2 = font(20, "bold")
    tw = tracked_width(d, SLOGAN, f2, 3)
    if source_note:
        d.text((MARGIN, y_line + 26), source_note, font=font(28, "semibold"), fill=MUTED)
        sx = W - MARGIN - tw
    else:
        sx = MARGIN + 22
    draw_tracked(d, sx, y_line + 32, SLOGAN, f2, SOFT, 3)
    d.rectangle([sx - 22, y_line + 32, sx - 14, y_line + 52], fill=RED)


# ---------------- Şablonlar ----------------
def _overlay(photo, category, headline, summary, highlight, source_note, date):
    img = background(photo, (W, H), base_y=680)
    mask = ImageChops.lighter(
        gradient_mask((W, H), 300, 730, 0.0, 0.97),
        gradient_mask((W, H), 0, 280, 0.62, 0.0),
    )
    img = darken(img, mask)
    d = ImageDraw.Draw(img)
    draw_topbar(img, date)

    max_w = W - 2 * MARGIN
    footer_y = 1244
    s_words = summary.split()
    sf, ssize, s_lines, s_trunc = fit_block(d, s_words, "regular", max_w, 36, 28, 4, 1.38)
    s_h = len(s_lines) * int(ssize * 1.38) if s_words else 0
    s_top = footer_y - 36 - s_h

    h_words = headline.split()
    hf, hsize, h_lines, h_trunc = fit_block(
        d, h_words, "extrabold", max_w, 88, 50, 6, 1.12, max_h=(s_top - 36) - 600)
    h_h = len(h_lines) * int(hsize * 1.12)
    h_top = s_top - 36 - h_h

    draw_tag(img, MARGIN, h_top - 28 - 48, category)
    hl = highlight_indices(h_words, highlight)
    draw_lines(d, MARGIN, h_top, h_words, h_lines, hf, hsize, 1.12,
               lambda i: ELECTRIC if i in hl else WHITE, h_trunc)
    if s_words:
        draw_lines(d, MARGIN, s_top, s_words, s_lines, sf, ssize, 1.38,
                   lambda i: MUTED, s_trunc)
    draw_footer(img, footer_y, source_note)
    return img


def _panel(photo, category, headline, summary, highlight, source_note, date):
    photo_h = 700
    top = background(photo, (W, photo_h), base_y=photo_h - 4)
    top = darken(top, gradient_mask((W, photo_h), 0, 260, 0.62, 0.0))
    img = Image.new("RGB", (W, H), NAVY)
    img.paste(top, (0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, photo_h, W, photo_h + 8], fill=ELECTRIC)
    d.rectangle([0, photo_h, 230, photo_h + 8], fill=RED)
    draw_topbar(img, date)

    max_w = W - 2 * MARGIN
    footer_y = 1244
    tag_y = photo_h + 44
    draw_tag(img, MARGIN, tag_y, category)
    y = tag_y + 48 + 26
    bottom = footer_y - 32

    s_words = summary.split()
    h_words = headline.split()
    hf, hsize, h_lines, h_trunc = fit_block(d, h_words, "extrabold", max_w, 66, 44, 4, 1.14, max_h=250)
    hl = highlight_indices(h_words, highlight)
    y = draw_lines(d, MARGIN, y, h_words, h_lines, hf, hsize, 1.14,
                   lambda i: ELECTRIC if i in hl else WHITE, h_trunc)
    y += 32
    if s_words and bottom - y > 60:
        sf, ssize, s_lines, s_trunc = fit_block(
            d, s_words, "regular", max_w, 32, 26, 4, 1.38, max_h=bottom - y)
        draw_lines(d, MARGIN, y, s_words, s_lines, sf, ssize, 1.38,
                   lambda i: MUTED, s_trunc)
    draw_footer(img, footer_y, source_note)
    return img


def baku_today():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=4)).date()


def make_post(photo, category, headline, summary, out, variant="overlay",
              highlight=None, source_note="", date=None):
    date = date or baku_today()
    category = az_upper(category) if category else ""
    builder = _panel if variant == "panel" else _overlay
    img = builder(photo, category, headline.strip(), summary.strip(),
                  highlight or [], source_note, date)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if out.lower().endswith((".jpg", ".jpeg")):
        img.save(out, quality=93, subsampling=0)
    else:
        img.save(out)
    return out


if __name__ == "__main__":
    # Nümunə şəkillər və loqo (yalnız yoxlama üçün)
    make_profile("profile_reportbaku.png")
    draw_logo(512, "light").save("logo_rb.png")
    demo = dict(
        photo=None,
        category="SİYASƏT",
        headline="Nümunə başlıq: xəbərin əsas fikri bu hissədə qısa şəkildə yazılır",
        summary="Bura xəbərin iki-üç cümləlik qısa xülasəsi yazılır. Mətn mənbədən köçürülmür, "
                "redaksiya tərəfindən öz sözlərimizlə hazırlanır.",
        highlight=["əsas fikri"],
        source_note="Mənbə: AZƏRTAC",
    )
    make_post(out="post_overlay.png", variant="overlay", **demo)
    make_post(out="post_panel.png", variant="panel", **demo)
    print("Hazırdır: profile_reportbaku.png, logo_rb.png, post_overlay.png, post_panel.png")
