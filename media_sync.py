"""
media budağının qovluğunu yeniləyir: köhnə videoları silir, yeniləri əlavə edir.

İstifadə: python media_sync.py <media_qovluğu> <yeni_videolar_qovluğu>

Video fayllarının adı <YYYYMMDD>-<HHMMSS>-... ilə başlayır (Bakı vaxtı). Bundan köhnə (48 saat)
və ya tanınmayan adlı fayllar silinir ki, repo böyüməsin.
"""

import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEEP_HOURS = 48


def main(media_dir, new_dir):
    media, new = Path(media_dir), Path(new_dir)
    media.mkdir(parents=True, exist_ok=True)
    now_baku = datetime.now(timezone.utc) + timedelta(hours=4)

    for f in media.glob("*.mp4"):
        m = re.match(r"(\d{8})-(\d{6})", f.name)
        if not m:
            f.unlink()
            print("Silindi (tanınmayan ad):", f.name)
            continue
        ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        if now_baku - ts > timedelta(hours=KEEP_HOURS):
            f.unlink()
            print("Silindi (köhnə):", f.name)

    for f in new.glob("*.mp4"):
        shutil.copy2(f, media / f.name)
        print("Əlavə olundu:", f.name)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("İstifadə: python media_sync.py <media_qovluğu> <yeni_videolar_qovluğu>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
