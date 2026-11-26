"""
ReportBaku - həftəlik statistika hesabatı (Telegram-a).

- İzləyici sayı və dəyişmə (stats.json-da gündəlik qeyd saxlanılır),
- sistemin paylaşdığı son 14 günün postları üçün Instagram insights (əhatə, baxış, bəyənmə, şərh, paylaşım, yadda saxlama),
- format (Reels/Post/Karusel) və kateqoriya üzrə orta göstərici, ən yaxşı 3 post, ən yaxşı paylaşım saatları.
İnsights alınmazsa (icazə yoxdur və s.), səbəb mesajda yazılır.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
IG_TOKEN = os.environ.get("INSTAGRAM_TOKEN", "")
IG_USER_ID = os.environ.get("INSTAGRAM_USER_ID", "")
PENDING = Path("pending.json")
STATS = Path("stats.json")
GRAPH = "https://graph.facebook.com/"
METRIC_SETS = ["views,reach,likes,comments,shares,saved,total_interactions",
               "reach,likes,comments,shares,saved,total_interactions",
               "reach,saved,shares"]
LABEL = {"reel": "Reels", "post": "Foto post", "carousel": "Karusel"}


def api(path, params):
    params = dict(params, access_token=IG_TOKEN)
    try:
        with urllib.request.urlopen(GRAPH + path + "?" + urllib.parse.urlencode(params), timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        return {"error": "HTTP %s %s" % (e.code, msg[:180])}
    except Exception as e:
        return {"error": type(e).__name__}


def insights(media_id):
    last = ""
    for metrics in METRIC_SETS:
        res = api(media_id + "/insights", {"metric": metrics})
        if "data" in res:
            return {d["name"]: (d.get("values") or [{}])[0].get("value", 0) for d in res["data"]}, ""
        last = res.get("error", "")
    return None, last


def tg(text):
    if not (TG_TOKEN and TG_CHAT):
        print(text)
        return
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN, data=data, timeout=60)


def avg(vals):
    return sum(vals) / len(vals) if vals else 0


def main():
    now = datetime.now(timezone.utc)
    hist = json.loads(STATS.read_text(encoding="utf-8")) if STATS.exists() else {"snapshots": [], "posts": {}}
    lines = ["📊 HƏFTƏLİK HESABAT (%s)" % (now + timedelta(hours=4)).strftime("%d.%m.%Y")]

    acct = api(IG_USER_ID, {"fields": "followers_count,media_count"})
    if "followers_count" in acct:
        today = {"date": now.date().isoformat(), "followers": acct["followers_count"], "media": acct.get("media_count")}
        hist["snapshots"] = [s for s in hist["snapshots"] if s["date"] != today["date"]] + [today]
        week_ago = [s for s in hist["snapshots"] if (now.date() - datetime.fromisoformat(s["date"]).date()).days >= 6]
        delta = ("%+d son 7 gündə" % (today["followers"] - week_ago[-1]["followers"])) if week_ago else "dəyişmə: ilk qeyd"
        lines.append("👥 İzləyici: %d (%s)" % (today["followers"], delta))
    else:
        lines.append("👥 İzləyici sayı alınmadı: %s" % acct.get("error", ""))

    pending = json.loads(PENDING.read_text(encoding="utf-8")) if PENDING.exists() else []
    cutoff = now - timedelta(days=14)
    posts = [p for p in pending if p.get("status") == "published" and p.get("instagram_id") and p.get("kind") != "story"
             and datetime.fromisoformat(p.get("published_at") or p["created"]) >= cutoff]
    rows, error = [], ""
    for p in posts:
        m, err = insights(p["instagram_id"])
        if m is None:
            error = err
            continue
        hist["posts"][p["instagram_id"]] = {"kind": p.get("kind", "post"), "category": p.get("category"),
                                             "headline": p["headline"][:80], "metrics": m,
                                             "hour": ((datetime.fromisoformat(p.get("published_at") or p["created"]) + timedelta(hours=4)).hour)}
        rows.append(hist["posts"][p["instagram_id"]])
    lines.append("📝 Son 14 gündə paylaşılan: %d, insights alınan: %d" % (len(posts), len(rows)))
    if error and not rows and posts:
        lines.append("⚠️ Insights alınmadı: %s\n(Çox güman ki, tokenə instagram_manage_insights icazəsi lazımdır.)" % error)
    if rows:
        score = lambda r: r["metrics"].get("views") or r["metrics"].get("reach") or 0
        by_kind, by_cat, by_hour = defaultdict(list), defaultdict(list), defaultdict(list)
        for r in rows:
            by_kind[r["kind"]].append(r); by_cat[r["category"]].append(r); by_hour[r["hour"]].append(r)
        lines.append("\n📌 Format üzrə orta (baxış/əhatə | bəyənmə | paylaşım | yadda saxlama):")
        for k, rs in sorted(by_kind.items(), key=lambda x: -avg([score(r) for r in x[1]])):
            lines.append("• %s (%d): %d | %d | %d | %d" % (LABEL.get(k, k), len(rs), avg([score(r) for r in rs]),
                         avg([r["metrics"].get("likes", 0) for r in rs]), avg([r["metrics"].get("shares", 0) for r in rs]),
                         avg([r["metrics"].get("saved", 0) for r in rs])))
        lines.append("\n🗂 Kateqoriya üzrə orta baxış/əhatə:")
        for c, rs in sorted(by_cat.items(), key=lambda x: -avg([score(r) for r in x[1]]))[:5]:
            lines.append("• %s (%d): %d" % (c, len(rs), avg([score(r) for r in rs])))
        if len(by_hour) > 1:
            best = sorted(by_hour.items(), key=lambda x: -avg([score(r) for r in x[1]]))[:3]
            lines.append("\n🕒 Ən yaxşı paylaşım saatları (Bakı): " + ", ".join("%02d:00" % h for h, _ in best))
        lines.append("\n🏆 Ən yaxşı 3 post:")
        for r in sorted(rows, key=score, reverse=True)[:3]:
            lines.append("• %s — %d (%s)" % (r["headline"], score(r), LABEL.get(r["kind"], r["kind"])))
        top_k = sorted(by_kind.items(), key=lambda x: -avg([score(r) for r in x[1]]))[0][0]
        lines.append("\n💡 Bu həftə ən güclü format: %s. Ona üstünlük verin." % LABEL.get(top_k, top_k))
    STATS.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    tg("\n".join(lines))


if __name__ == "__main__":
    main()
