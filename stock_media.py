"""
ReportBaku - mövzuya uyğun ÜMUMİ (stok) video/foto tapır (video fonunda kiçik pəncərə üçün).

Mənbələr (açarı olan istifadə olunur, sıra ilə):
  1) Pixabay  (PIXABAY_API_KEY)  - pulsuz; API qaydası: mənbəni göstərmək (caption-da yazılır)
  2) Pexels   (PEXELS_API_KEY)   - ehtiyat; hazırda yeni açar verilmir, köhnə açarınız varsa işləyir

Açar yoxdursa və ya nəticə tapılmasa None qaytarır (video markalı Bakı fonu ilə çıxır).
"""

import hashlib
import os
import random
from pathlib import Path

import requests

PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
MAX_MB = 30
LAST_ERROR = ""      # son uğursuzluğun səbəbi (diaqnostika üçün)


def _fail(msg):
    global LAST_ERROR
    LAST_ERROR = msg


def _download(url, path):
    try:
        with requests.get(url, stream=True, timeout=90) as r:
            if not r.ok:
                return False
            size = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    size += len(chunk)
                    if size > MAX_MB * 1024 * 1024:
                        return False
                    f.write(chunk)
        return size > 10000
    except Exception:
        return False


# ---------------- Pixabay ----------------
def _pixabay_video(kw, rnd, out_dir, name):
    try:
        r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                         params={"key": PIXABAY_KEY, "q": kw, "per_page": 20, "safesearch": "true"})
        hits = r.json().get("hits", []) if r.ok else []
        if not r.ok:
            _fail("Pixabay HTTP %s" % getattr(r, "status_code", "?"))
    except Exception:
        return None
    hits = [h for h in hits if 4 <= (h.get("duration") or 0) <= 40]
    if not hits:
        return None
    h = rnd.choice(hits[:10])
    for quality in ("small", "medium", "tiny", "large"):        # pəncərə üçün kiçik fayl kifayətdir
        v = (h.get("videos") or {}).get(quality) or {}
        if v.get("url") and (v.get("size") or 0) <= MAX_MB * 1024 * 1024:
            path = Path(out_dir) / (name + ".mp4")
            if _download(v["url"], path):
                return {"path": str(path), "kind": "video", "credit": "Pixabay", "keyword": kw}
    return None


def _pixabay_photo(kw, rnd, out_dir, name):
    try:
        r = requests.get("https://pixabay.com/api/", timeout=30,
                         params={"key": PIXABAY_KEY, "q": kw, "image_type": "photo",
                                 "orientation": "horizontal", "per_page": 20, "safesearch": "true"})
        hits = r.json().get("hits", []) if r.ok else []
    except Exception:
        return None
    if not hits:
        return None
    h = rnd.choice(hits[:10])
    url = h.get("largeImageURL") or h.get("webformatURL")
    path = Path(out_dir) / (name + ".jpg")
    if url and _download(url, path):
        return {"path": str(path), "kind": "photo", "credit": "Pixabay", "keyword": kw}
    return None


# ---------------- Pexels (ehtiyat) ----------------
def _pexels_video(kw, rnd, out_dir, name):
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS_KEY},
                         timeout=30, params={"query": kw, "orientation": "landscape", "size": "medium",
                                             "per_page": 12})
        videos = r.json().get("videos", []) if r.ok else []
    except Exception:
        return None
    videos = [v for v in videos if 4 <= (v.get("duration") or 0) <= 40]
    if not videos:
        return None
    v = rnd.choice(videos[:8])
    files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
    files = [f for f in files if 540 <= f["width"] <= 1400] or files
    if not files:
        return None
    f = min(files, key=lambda x: abs(x["width"] - 960))
    path = Path(out_dir) / (name + ".mp4")
    if _download(f["link"], path):
        return {"path": str(path), "kind": "video", "credit": "Pexels", "keyword": kw}
    return None


def _pexels_photo(kw, rnd, out_dir, name):
    try:
        r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": PEXELS_KEY},
                         timeout=30, params={"query": kw, "orientation": "landscape", "per_page": 12})
        photos = r.json().get("photos", []) if r.ok else []
    except Exception:
        return None
    if not photos:
        return None
    ph = rnd.choice(photos[:8])
    url = (ph.get("src") or {}).get("large") or (ph.get("src") or {}).get("medium")
    path = Path(out_dir) / (name + ".jpg")
    if url and _download(url, path):
        return {"path": str(path), "kind": "photo", "credit": "Pexels", "keyword": kw}
    return None


def find_clip(keywords, seed, out_dir, name):
    """{'path':..., 'kind': 'video'|'photo', 'credit': 'Pixabay'|'Pexels', 'keyword':...} və ya None."""
    providers = []
    if PIXABAY_KEY:
        providers.append((_pixabay_video, _pixabay_photo))
    if PEXELS_KEY:
        providers.append((_pexels_video, _pexels_photo))
    if not providers or not keywords:
        return None
    rnd = random.Random(int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16))
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for video_fn, _ in providers:                     # əvvəl video
        for kw in keywords:
            res = video_fn(kw, rnd, out_dir, name)
            if res:
                return res
    for _, photo_fn in providers:                     # tapılmasa foto
        for kw in keywords:
            res = photo_fn(kw, rnd, out_dir, name)
            if res:
                return res
    return None
