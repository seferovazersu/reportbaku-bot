"""Skripti işlədir; xəta olarsa son sətirlərini Telegram-a göndərir (loqa baxmaq lazım olmasın).
İstifadə: python run_safe.py <skript.py>"""
import os
import runpy
import sys
import traceback
import urllib.parse
import urllib.request

script = sys.argv[1]
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as e:
    if e.code not in (0, None):
        raise
except BaseException:
    tb = traceback.format_exc()
    print(tb, file=sys.stderr)
    token, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat:
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": ("⚠️ XƏTA (%s):\n%s" % (script, tb[-1500:]))}).encode()
            urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % token, data=data, timeout=30)
        except Exception:
            pass
    sys.exit(1)
