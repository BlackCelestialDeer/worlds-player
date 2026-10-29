# One pass: read Caedrel's latest uploads and merge them into feed.json.
# Sources, in order: YouTube's channel RSS, the uploads-playlist RSS, and as a fallback the channel's
# Videos page. RSS regularly fails or lags for hours; the page is slower to parse but rarely down.
# Exit code 0 when at least one source worked, 1 when all failed (feed.json is then left as it was).
import json, os, re, sys, time, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

CHANNEL = "UCOFiUtKui6-x4T-J7_DgCag"   # @Caedrel (main channel)
RSS = [f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}",
       f"https://www.youtube.com/feeds/videos.xml?playlist_id=UU{CHANNEL[2:]}"]
PAGE = "https://www.youtube.com/@Caedrel/videos"
SINCE = "2026-10-01"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


ERRORS = []
DIAG = []


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
            return urllib.request.urlopen(req, timeout=30).read()
        except Exception as e:
            print(f"  {url[-45:]}: try {i+1} failed: {e}")
            ERRORS.append(str(e)[:60])
            time.sleep(10)
    return None


def from_rss(xml):
    out = {}
    for e in ET.fromstring(xml).findall("a:entry", NS):
        vid = e.findtext("yt:videoId", default="", namespaces=NS)
        if vid:
            out[vid] = {"id": vid, "title": e.findtext("a:title", default="", namespaces=NS),
                        "published": e.findtext("a:published", default="", namespaces=NS)}
    return out


def ago(text, now):
    # "5 hours ago", "Streamed 2 days ago" -> approximate ISO time (only used when RSS doesn't know the video).
    m = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", text or "")
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2)
    secs = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}[unit]
    return (now - timedelta(seconds=n * secs)).replace(microsecond=0).isoformat()


def from_page(html):
    m = re.search(rb"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        return {}
    data, out, now = json.loads(m.group(1)), {}, datetime.now(timezone.utc)

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if isinstance(v, dict) and v.get("videoId"):
                title = "".join(r.get("text", "") for r in v.get("title", {}).get("runs", []))
                pub = ago((v.get("publishedTimeText") or {}).get("simpleText", ""), now)
                out[v["videoId"]] = {"id": v["videoId"], "title": title, "published": pub, "approx": True}
            lv = o.get("lockupViewModel")   # the newer page format
            if isinstance(lv, dict) and lv.get("contentId") and "VIDEO" in str(lv.get("contentType", "VIDEO")):
                meta = (lv.get("metadata") or {}).get("lockupMetadataViewModel") or {}
                title = (meta.get("title") or {}).get("content", "")
                texts = re.findall(r'"content":\s*"([^"]*ago)"', json.dumps(meta, ensure_ascii=False))
                pub = ago(texts[0] if texts else "", now)
                if texts and not pub and not DIAG:
                    DIAG.append("ago-fail " + repr(texts[0][-40:]))
                if not texts and not DIAG:
                    t = json.dumps(meta, ensure_ascii=False).replace(title, "<title>")
                    DIAG.append(re.sub(r"[A-Za-z]{12,}", "~", t)[:400])
                if title:
                    out[lv["contentId"]] = {"id": lv["contentId"], "title": title, "published": pub, "approx": True}
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(data)
    return out


fresh, worked, report = {}, False, []
for name, url in zip(["rss-channel", "rss-playlist"], RSS):
    x = get(url)
    if x:
        try:
            got = from_rss(x); fresh.update(got); worked = True
            report.append(f"{name} {len(got)}")
        except Exception as e:
            print(f"  RSS parse failed: {e}"); report.append(f"{name} parse-error")
    else:
        report.append(f"{name} down")
page = get(PAGE, tries=2)
if not page:
    report.append("page down")
if page:
    try:
        found = from_page(page)
        report.append(f"page {len(found)}")
        if not found:   # what did we get instead? (consent wall, new page format...)
            low = page.lower()
            report.append("page-shape " + " ".join(f"{k}={low.count(k.encode())}" for k in
                          ["ytinitialdata", "videorenderer", "lockupviewmodel", "videoid", "consent", "captcha"]) + f" len={len(page)}")
        worked = worked or bool(found)
        for vid, v in found.items():
            fresh.setdefault(vid, v)   # RSS data (exact time) wins over the page's approximate time
    except Exception as e:
        print(f"  page parse failed: {e}")

# Shown as an annotation on the run, readable without opening the logs.
print("::notice title=feed sources::" + ", ".join(report) + (f"; errors: {sorted(set(ERRORS))[:3]}" if ERRORS else "") + (f"; diag {DIAG[0]}" if DIAG else "") + f"; newest {max((v['published'] for v in fresh.values()), default='-')[:16]}")
if not worked:
    print("All YouTube sources failed; feed.json unchanged")
    sys.exit(1)

old = {}
if os.path.exists("feed.json"):
    try:
        old = {v["id"]: v for v in json.load(open("feed.json")).get("videos", [])}
    except Exception:
        old = {}
merged = dict(old)
for vid, v in fresh.items():
    if not v.get("published"):
        continue
    if vid in old and not old[vid].get("approx") and v.get("approx"):
        merged[vid]["title"] = v["title"]       # keep the exact time, take a renamed title
    else:
        merged[vid] = v
videos = sorted((v for v in merged.values() if v["published"] >= SINCE), key=lambda v: v["published"], reverse=True)[:400]
text = json.dumps({"channel": "@Caedrel", "channelId": CHANNEL, "videos": videos}, ensure_ascii=False, indent=1)
if not os.path.exists("feed.json") or open("feed.json").read() != text:
    open("feed.json", "w").write(text)
    print(f"feed.json updated: {len(videos)} uploads")
else:
    print("no new uploads")
