# Builds data.json for the schedule page: every series with its broadcast day, teams, result and
# Caedrel's YouTube upload. All logic is code; nothing is guessed by a model.
#
# Inputs:  lolesports schedule (live), structure.json (our series ids, labels, dependencies),
#          feed.json (Caedrel's uploads), rosters.json (player -> team, refreshed daily).
# Output:  data.json. If the schedule can't be read, the previous data is kept and the failure
#          is recorded in data.json's status so the page can show it.
import json, os, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

AMS = ZoneInfo("Europe/Amsterdam")
API = "https://esports-api.lolesports.com/persisted/gw/"
KEY = {"x-api-key": "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"}   # the public key lolesports.com itself uses
SINCE = "2026-10-01"
CODE_FIX = {"TLAW": "TL"}                       # lolesports code -> code used on the page
STAGE = {"Play-Ins": "playin", "Swiss": "swiss", "Quarterfinals": "ko", "Semifinals": "ko", "Finals": "ko"}
BASE = {"playin": 100, "swiss": 200, "ko": 300}
BLOCK_GAP = timedelta(hours=8)                  # series closer together than this share a broadcast day

# Extra names fans and titles use for teams (on top of code and full name).
ALIASES = {"GEN": ["GENG", "GEN.G", "GEN G"], "MKOI": ["KOI", "MAD", "MOVISTAR KOI"], "DK": ["DPLUS", "DPLUS KIA", "DWG"],
           "TL": ["TLAW", "LIQUID", "TEAM LIQUID"], "FUR": ["FURIA"], "IG": ["INVICTUS"], "TES": ["TOP ESPORTS"],
           "BLG": ["BILIBILI"], "HLE": ["HANWHA", "HANWHA LIFE"], "KC": ["KARMINE", "KCORP", "KARMINE CORP"],
           "AL": ["ANYONE'S LEGEND", "ANYONES LEGEND"], "LOS": ["LOUD"], "CFO": ["CTBC", "FLYING OYSTER"],
           "TSW": ["TEAM SECRET WHALES", "SECRET WHALES"], "LYON": [], "G2": [], "T1": []}
# Words in titles that are also player names or too common to anchor on.
STOP = set("THE A AN AND OR TO OF ON IN AT FOR VS WORLDS 2026 TAKE TAKES DO DIE GOES HOME LINE SPOT TOP FIRST TEAM MAKE "
           "CAN WINNER LOSER FINAL BEST ONES TOURNAMENT MEET AGAIN SURVIVE UNTHINKABLE ANOTHER PROMOTION EUROPE NA LCP "
           "LCK LPL LEC LCS CBLOL DOWN KING GOD BACK LAST GAME GAMES DAY PART SWISS STAGE ROUND WHO WILL IS IT".split())
SKIP_TITLE = re.compile(r"\b(DRAW|PREDICTION|PREDICTIONS|PICK'?EMS?|TIER ?LIST|RECAP|PODCAST|POWER RANK)", re.I)


def get(path, tries=3):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(API + path, headers=KEY), timeout=60))
        except Exception as e:
            print(f"  {path[:40]}: try {i+1} failed: {e}")
            time.sleep(10)
    return None


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def fetch_events():
    lg = get("getLeagues?hl=en-GB")
    if not lg:
        return None
    lid = [l for l in lg["data"]["leagues"] if l["slug"] == "worlds"][0]["id"]
    first = get(f"getSchedule?hl=en-GB&leagueId={lid}")
    if not first:
        return None
    pages = [first["data"]["schedule"]]
    for d in ("older", "newer"):
        tok = pages[0]["pages"].get(d)
        while tok and len(pages) < 12:
            s = get(f"getSchedule?hl=en-GB&leagueId={lid}&pageToken={tok}")
            if not s:
                return None
            s = s["data"]["schedule"]; pages.append(s); tok = s["pages"].get(d)
    evs = {e["match"]["id"]: e for p in pages for e in p["events"]
           if e.get("type") == "match" and e.get("startTime", "") >= SINCE}
    return sorted(evs.values(), key=lambda e: (e["startTime"], e["match"]["id"]))


def refresh_rosters(codes):
    """player name (upper) -> team code, from lolesports team pages. Refreshed at most once a day."""
    cur = load("rosters.json", {})
    if cur.get("fetched", "") > (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat() and cur.get("players"):
        return cur["players"]
    d = get("getTeams?hl=en-GB")
    if not d:
        return cur.get("players", {})
    players = {}
    for t in d["data"]["teams"]:
        code = CODE_FIX.get(t.get("code"), t.get("code"))
        if code in codes and t.get("status", "active") == "active":
            for p in t.get("players") or []:
                name = (p.get("summonerName") or "").upper().strip()
                if len(name) >= 3 and name not in STOP:
                    players[name] = code
    if players:
        json.dump({"fetched": datetime.now(timezone.utc).isoformat(), "players": players},
                  open("rosters.json", "w"), indent=1, sort_keys=True, ensure_ascii=False)
    return players or cur.get("players", {})


def build_series(events, struct):
    series, blocks = [], []
    for e in events:
        t = datetime.fromisoformat(e["startTime"].replace("Z", "+00:00"))
        if not blocks or t - blocks[-1]["last"] >= BLOCK_GAP:
            blocks.append({"start": t, "last": t})
        blocks[-1]["last"] = t
        e["_block"] = blocks[-1]["start"]
    counters = {}
    for e in events:
        sid = struct.get(e["match"]["id"])
        if not sid:
            print(f"  unknown series {e['match']['id']} ({e.get('blockName')}): not in structure.json")
            continue
        stage = STAGE.get(e.get("blockName"), "ko")
        counters[stage] = counters.get(stage, 0) + 1
        start = e["_block"].astimezone(AMS)
        teams = e["match"]["teams"]
        code = lambda x: "" if x.get("code") in (None, "", "TBD") else CODE_FIX.get(x["code"], x["code"])
        a, b = code(teams[0]), code(teams[1])
        done = e.get("state") == "completed"
        res = [x.get("result") or {} for x in teams]
        winner = a if res[0].get("outcome") == "win" else b if res[1].get("outcome") == "win" else ""
        series.append({
            "id": sid["id"], "order": BASE[stage] + counters[stage] - 1, "stage": stage, "label": sid["label"],
            "format": f"Bo{e['match'].get('strategy', {}).get('count', '')}", "day": start.strftime("%Y-%m-%d"),
            "time": start.strftime("%H:%M"), "tentative": False, "teamA": a, "teamB": b,
            "played": done, "winner": winner if done else "",
            "score": f"{res[0].get('gameWins', 0)}-{res[1].get('gameWins', 0)}" if done else "",
            "dependsOn": sid["dependsOn"], "videoId": "", "dayVideos": [],
            "_start": e["_block"], "_t": e["startTime"]})
    return series


def anchors(title, aliases, players):
    """Team codes a title refers to, by team code/name/alias or player name."""
    up = " " + re.sub(r"[^A-Z0-9.' ]", " ", title.upper()) + " "
    words = set(up.split())
    found = set()
    for code, names in aliases.items():
        for n in names:
            if (" " in n and f" {n} " in up) or n in words:
                found.add(code)
    for name, code in players.items():
        if name in words:
            found.add(code)
    return found


def match_videos(series, feed, players):
    codes = {s["teamA"] for s in series} | {s["teamB"] for s in series}
    codes.discard("")
    aliases = {c: [c] + ALIASES.get(c, []) for c in codes}
    starts = sorted({s["_start"] for s in series})
    by_block = {}
    for s in series:
        by_block.setdefault(s["_start"], []).append(s)
    stage_days = {}                                   # stage -> ordered broadcast day starts (for "DAY n")
    for b in starts:
        for st in {s["stage"] for s in by_block[b]}:
            stage_days.setdefault(st, []).append(b)
    covers, day_parts = {}, {}
    for v in sorted(feed, key=lambda v: v.get("published", "")):
        try:
            pub = datetime.fromisoformat(v["published"].replace("Z", "+00:00"))
        except Exception:
            continue
        cand = [b for b in starts if b <= pub < b + timedelta(hours=40)]
        if not cand:
            continue
        block = max(cand)
        title = v.get("title", "")
        if SKIP_TITLE.search(title):
            continue
        day = re.search(r"\bDAY\s*(\d+)\b", title, re.I)
        part = re.search(r"\bPART\s*(\d+)\b", title, re.I)
        found = anchors(title, aliases, players)
        if day and (part or not found):
            stage = "swiss" if re.search(r"SWISS", title, re.I) else "playin" if re.search(r"PLAY.?IN", title, re.I) else by_block[block][0]["stage"]
            days = stage_days.get(stage, [])
            n = int(day.group(1))
            if 1 <= n <= len(days):
                day_parts.setdefault(days[n - 1], []).append((int(part.group(1)) if part else 1, v["id"]))
            continue
        todays = by_block[block]
        hit = []
        for s in todays:
            if (s["teamA"] in found or s["teamB"] in found):
                # a team named in the title must play only this series today, otherwise it's ambiguous
                named = found & {s["teamA"], s["teamB"]}
                if all(sum(1 for x in todays if c in (x["teamA"], x["teamB"])) == 1 for c in named):
                    hit.append(s["id"])
        if not hit or len(hit) > 4:
            continue
        covers[v["id"]] = {"pub": v["published"], "ids": hit, "block": block}
    # fill gaps: a series between two series of the same upload (in play order) belongs to it
    for b, todays in by_block.items():
        ordered = [s["id"] for s in sorted(todays, key=lambda s: s["order"])]
        owner = {}
        for vid, c in covers.items():
            if c["block"] == b:
                for i in c["ids"]:
                    owner.setdefault(i, []).append(vid)
        for k, sid in enumerate(ordered):
            if sid in owner:
                continue
            before = next((owner[x] for x in reversed(ordered[:k]) if x in owner), [])
            after = next((owner[x] for x in ordered[k + 1:] if x in owner), [])
            common = [v for v in before if v in after]
            if len(common) == 1:
                covers[common[0]]["ids"].append(sid)
    # link: per series the upload covering the fewest series, then the earliest
    best = {}
    for vid, c in covers.items():
        for sid in c["ids"]:
            key = (len(c["ids"]), c["pub"])
            if sid not in best or key < best[sid][0]:
                best[sid] = (key, vid)
    for s in series:
        if s["id"] in best:
            s["videoId"] = best[s["id"]][1]
        parts = sorted(set(day_parts.get(s["_start"], [])))
        s["dayVideos"] = [vid for _, vid in parts]


def main():
    struct = load("structure.json", {})
    old = load("data.json", {})
    status = old.get("status", {})
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    events = fetch_events()
    if events is None:
        status["schedule"] = {"ok": False, "at": now}
        if old.get("series"):
            old["status"] = status
            json.dump(old, open("data.json", "w"), indent=1, ensure_ascii=False)
        print("schedule unavailable; previous data kept")
        sys.exit(1)
    series = build_series(events, struct)
    codes = {c for s in series for c in (s["teamA"], s["teamB"]) if c}
    players = refresh_rosters(codes)
    feed = load("feed.json", {}).get("videos", [])
    match_videos(series, feed, players)
    for s in series:
        s.pop("_start", None); s.pop("_t", None)
    status["schedule"] = {"ok": True, "at": now}
    fs = load("feed_status.json", {})
    if fs:
        status["feed"] = fs
    # Heartbeat: the hour of the last successful check, so data.json changes (and is committed) at most
    # once an hour when nothing else changes. The page shows it as "data checked".
    status["checked"] = now[:13] + ":00:00+00:00"
    status.pop("changedAt", None)
    out = {"series": sorted(series, key=lambda s: s["order"]), "status": status}
    json.dump(out, open("data.json", "w"), indent=1, ensure_ascii=False)
    print(f"data.json: {len(series)} series, {sum(1 for s in series if s['videoId'])} with a video")


if __name__ == "__main__":
    main()
