"""
Mundial 2026 – Backend ESPN API
Gratuito, sin API key requerida
"""

import os, time, asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import httpx

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/FIFA.World/scoreboard"
ESPN_STANDINGS  = "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings"

MEXICO_ID = "203"
USA_ID    = "660"
CANADA_ID = "206"

_cache: dict = {}
CACHE_TTL = 60  # segundos

def cache_get(key):
    e = _cache.get(key)
    if e and time.time() - e["ts"] < CACHE_TTL:
        return e["data"]
    return None

def cache_set(key, data):
    _cache[key] = {"data": data, "ts": time.time()}

app = FastAPI(title="Mundial 2026")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

async def fetch_all_events() -> list:
    cached = cache_get("all_events")
    if cached is not None:
        return cached

    chunks = [
        "20260611-20260621",
        "20260622-20260630",
        "20260701-20260710",
        "20260711-20260719",
    ]

    async def fetch_chunk(client, chunk):
        try:
            r = await client.get(ESPN_SCOREBOARD, params={"dates": chunk, "limit": 100})
            r.raise_for_status()
            return r.json().get("events", [])
        except Exception as e:
            print(f"[ESPN] chunk {chunk} error: {e}", flush=True)
            return []

    async with httpx.AsyncClient(timeout=20) as client:
        results = await asyncio.gather(*[fetch_chunk(client, c) for c in chunks])

    all_events, seen = [], set()
    for evs in results:
        for ev in evs:
            if ev["id"] not in seen:
                seen.add(ev["id"])
                all_events.append(ev)

    all_events.sort(key=lambda e: e.get("date", ""))
    cache_set("all_events", all_events)
    print(f"[ESPN] cargados {len(all_events)} partidos", flush=True)
    return all_events

def stat(statistics: list, name: str) -> str:
    for s in statistics:
        if s.get("name") == name:
            return s.get("displayValue", "0")
    return "0"

def team_data(t: dict) -> dict:
    tm = t.get("team", {})
    return {
        "id":    tm.get("id", ""),
        "name":  tm.get("displayName", ""),
        "abbr":  tm.get("abbreviation", ""),
        "logo":  tm.get("logo", ""),
        "score": t.get("score", ""),
        "winner": t.get("winner", False),
        "stats": {
            "possession":   stat(t.get("statistics", []), "possessionPct"),
            "shots":        stat(t.get("statistics", []), "totalShots"),
            "sog":          stat(t.get("statistics", []), "shotsOnTarget"),
            "shot_assists": stat(t.get("statistics", []), "shotAssists"),
            "corners":      stat(t.get("statistics", []), "wonCorners"),
            "fouls":        stat(t.get("statistics", []), "foulsCommitted"),
            "goal_assists": stat(t.get("statistics", []), "goalAssists"),
        }
    }

def transform_event(ev: dict) -> dict:
    comp    = ev["competitions"][0]
    comps   = comp.get("competitors", [])
    home    = next((c for c in comps if c.get("homeAway") == "home"), comps[0] if comps else {})
    away    = next((c for c in comps if c.get("homeAway") == "away"), comps[1] if len(comps) > 1 else {})
    st      = comp.get("status", {})
    st_type = st.get("type", {})
    venue   = comp.get("venue", {})

    events_out = []
    for d in comp.get("details", []):
        athletes  = d.get("athletesInvolved", [])
        player    = athletes[0].get("displayName", "") if athletes else ""
        jersey    = athletes[0].get("jersey", "")     if athletes else ""
        type_text = d.get("type", {}).get("text", "").lower()
        minute    = d.get("clock", {}).get("displayValue", "")
        team_id   = d.get("team", {}).get("id", "")

        if d.get("scoringPlay"):
            etype = "own_goal" if d.get("ownGoal") else "penalty" if d.get("penaltyKick") else "goal"
        elif d.get("redCard"):
            etype = "red_card"
        elif d.get("yellowCard"):
            etype = "yellow_card"
        elif "substitution" in type_text or "sub" == type_text:
            player_in = athletes[1].get("displayName", "") if len(athletes) > 1 else ""
            events_out.append({
                "type": "sub", "minute": minute,
                "player": player, "player_in": player_in,
                "jersey": jersey, "team_id": team_id,
            })
            continue
        else:
            continue
        events_out.append({
            "type": etype, "minute": minute,
            "player": player, "jersey": jersey, "team_id": team_id,
        })

    return {
        "id":     ev["id"],
        "date":   ev["date"],
        "name":   ev.get("name", ""),
        "home":   team_data(home),
        "away":   team_data(away),
        "status": {
            "state":     st_type.get("state", "pre"),
            "detail":    st_type.get("detail", ""),
            "completed": st_type.get("completed", False),
            "clock":     st.get("displayClock", ""),
        },
        "venue":   venue.get("fullName", ""),
        "city":    venue.get("address", {}).get("city", ""),
        "country": venue.get("address", {}).get("country", ""),
        "group":   comp.get("altGameNote", ""),
        "slug":    ev.get("season", {}).get("slug", "group-stage"),
        "events":  events_out,
    }

# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "source": "ESPN"}

@app.get("/api/matches")
async def get_matches():
    evs = await fetch_all_events()
    return [transform_event(e) for e in evs]

@app.get("/api/match/{mid}")
async def get_match(mid: str):
    evs = await fetch_all_events()
    ev = next((e for e in evs if e["id"] == mid), None)
    if not ev:
        raise HTTPException(404, "Partido no encontrado")
    return transform_event(ev)

@app.get("/api/mexico")
async def get_mexico():
    evs = await fetch_all_events()
    filtered = [
        e for e in evs
        if any(c.get("team", {}).get("id") == MEXICO_ID
               for c in e["competitions"][0].get("competitors", []))
    ]
    return [transform_event(e) for e in filtered]

@app.get("/api/semifinals")
async def get_semifinals():
    evs = await fetch_all_events()
    filtered = [
        e for e in evs
        if "2026-07-14" in e.get("date", "")
        or "2026-07-15" in e.get("date", "")
        or "semi" in e.get("season", {}).get("slug", "").lower()
    ]
    return [transform_event(e) for e in filtered]

@app.get("/api/match/{mid}/lineup")
async def get_lineup(mid: str):
    cached = cache_get(f"lineup_{mid}")
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                "https://site.api.espn.com/apis/site/v2/sports/soccer/FIFA.World/summary",
                params={"event": mid}
            )
            r.raise_for_status()
            data = r.json()

        # ── Extract real substitution events from summary plays ─────────
        sub_out: set = set()
        sub_in:  set = set()

        # Try summary-level plays array (ESPN soccer summary often has this)
        for play in data.get("plays", []):
            type_id   = str(play.get("type", {}).get("id", ""))
            type_text = play.get("type", {}).get("text", "").lower()
            if "substitution" in type_text or type_id in ("58",):
                athl = play.get("athletesInvolved", [])
                if athl:       sub_out.add(athl[0].get("displayName", ""))
                if len(athl)>1: sub_in.add(athl[1].get("displayName", ""))

        # Also check header > competitions > details as fallback
        for comp in (data.get("header", {})
                         .get("competitions", [])):
            for d in comp.get("details", []):
                type_text = d.get("type", {}).get("text", "").lower()
                if "substitution" in type_text:
                    athl = d.get("athletesInvolved", [])
                    if athl:       sub_out.add(athl[0].get("displayName", ""))
                    if len(athl)>1: sub_in.add(athl[1].get("displayName", ""))

        # Also check the scoreboard cache for this match
        try:
            evs = await fetch_all_events()
            raw = next((e for e in evs if e["id"] == mid), None)
            if raw:
                comp0 = raw["competitions"][0]
                for d in comp0.get("details", []):
                    type_text = d.get("type", {}).get("text", "").lower()
                    if "substitution" in type_text:
                        athl = d.get("athletesInvolved", [])
                        if athl:       sub_out.add(athl[0].get("displayName", ""))
                        if len(athl)>1: sub_in.add(athl[1].get("displayName", ""))
        except Exception:
            pass

        have_events = bool(sub_out or sub_in)

        # ── Derive per-player stats from match events ───────────────────
        # Primary source: scoreboard cache (same data that drives the events
        # column in the UI — reliably populated for all finished matches).
        # Secondary: summary header details + plays (backup).

        player_stats: dict = {}   # name → {goals, assists, yellow_cards, red_cards, own_goals}
        sub_out_min:  dict = {}   # name → minute subbed off (starter)
        sub_in_min:   dict = {}   # name → minute subbed on  (bench)

        def _ps(name: str) -> dict:
            if name not in player_stats:
                player_stats[name] = {
                    "goals": 0, "assists": 0,
                    "yellow_cards": 0, "red_cards": 0,
                    "own_goals": 0,
                }
            return player_stats[name]

        def _minute(val: str) -> int:
            try:
                return int(str(val).split("+")[0].replace("'","").strip())
            except Exception:
                return 0

        def _parse_details(details: list) -> None:
            for d in details:
                athl      = d.get("athletesInvolved", [])
                type_text = d.get("type", {}).get("text", "").lower()
                if not athl:
                    continue
                pname = athl[0].get("displayName", "")
                if not pname:
                    continue

                if d.get("scoringPlay"):
                    if d.get("ownGoal"):
                        _ps(pname)["own_goals"] += 1
                    else:
                        _ps(pname)["goals"] += 1
                        if len(athl) > 1:
                            aname = athl[1].get("displayName", "")
                            if aname:
                                _ps(aname)["assists"] += 1
                elif d.get("yellowCard"):
                    _ps(pname)["yellow_cards"] += 1
                elif d.get("redCard"):
                    _ps(pname)["red_cards"] += 1
                elif "substitution" in type_text:
                    min_val = _minute(d.get("clock", {}).get("displayValue", "0"))
                    sub_out_min[pname] = min_val
                    if len(athl) > 1:
                        iname = athl[1].get("displayName", "")
                        if iname:
                            sub_in_min[iname] = min_val

        # 1) Scoreboard cache (primary — this is what populates the events column)
        try:
            evs     = await fetch_all_events()
            raw_ev  = next((e for e in evs if e["id"] == mid), None)
            if raw_ev:
                _parse_details(raw_ev["competitions"][0].get("details", []))
        except Exception:
            pass

        # 2) Summary header details (secondary)
        for comp in data.get("header", {}).get("competitions", []):
            _parse_details(comp.get("details", []))

        # 3) Plays array (substitution timing fallback)
        for play in data.get("plays", []):
            type_text = play.get("type", {}).get("text", "").lower()
            type_id   = str(play.get("type", {}).get("id", ""))
            if "substitution" in type_text or type_id == "58":
                athl    = play.get("athletesInvolved", [])
                min_val = _minute(play.get("clock", {}).get("displayValue", "0"))
                if athl:
                    sub_out_min[athl[0].get("displayName", "")] = min_val
                if len(athl) > 1:
                    iname = athl[1].get("displayName", "")
                    if iname:
                        sub_in_min[iname] = min_val

        def calc_rating(name: str, starter: bool) -> float | None:
            if starter:
                mins = sub_out_min.get(name, 90)
            else:
                if name not in sub_in_min:
                    return None   # never entered
                mins = 90 - sub_in_min.get(name, 90)

            ps = player_stats.get(name, {})
            s  = 6.0
            s += ps.get("goals",        0) * 1.5
            s += ps.get("assists",      0) * 0.8
            s -= ps.get("yellow_cards", 0) * 0.5
            s -= ps.get("red_cards",    0) * 2.0
            s -= ps.get("own_goals",    0) * 1.0
            if mins < 60:
                s -= 0.3
            return round(max(1.0, min(10.0, s)), 1)

        def has_notable(ps: dict) -> bool:
            return (ps.get("goals", 0) > 0
                    or ps.get("assists", 0) > 0
                    or ps.get("yellow_cards", 0) > 0
                    or ps.get("red_cards", 0) > 0
                    or ps.get("own_goals", 0) > 0)

        # ── Build roster ────────────────────────────────────────────────
        rosters = data.get("rosters", [])
        result  = []

        for t in rosters:
            team   = t.get("team", {})
            roster = t.get("roster", [])
            starters, bench = [], []

            for p in roster:
                ath     = p.get("athlete", {})
                pos     = ath.get("position", {})
                name    = ath.get("displayName", "")
                is_strt = p.get("starter", False)

                # Use event-derived subs when available; fall back to ESPN flags
                if have_events:
                    s_out = name in sub_out
                    s_in  = name in sub_in
                else:
                    s_out = p.get("subbedOut", False)
                    s_in  = p.get("subbedIn",  False)

                rtg = calc_rating(name, is_strt)
                ps  = player_stats.get(name, {})

                entry = {
                    "name":       name,
                    "short":      ath.get("shortName", ""),
                    "jersey":     ath.get("jersey", ""),
                    "position":   pos.get("abbreviation", ""),
                    "subbed_in":  s_in,
                    "subbed_out": s_out,
                    "order":      p.get("order", 99),
                    "rating":     rtg,
                    "goals":      ps.get("goals", 0),
                    "assists":    ps.get("assists", 0),
                    "yellow_cards": ps.get("yellow_cards", 0),
                    "red_cards":  ps.get("red_cards", 0),
                }
                if is_strt:
                    starters.append(entry)
                else:
                    bench.append(entry)

            starters.sort(key=lambda x: x["order"])

            # Top players = only those with notable events, sorted by rating
            all_players = starters + bench
            notable = [
                p for p in all_players
                if p.get("rating") is not None
                and has_notable(player_stats.get(p["name"], {}))
            ]
            top_players = sorted(
                notable,
                key=lambda p: (p["rating"], p.get("goals",0), p.get("assists",0)),
                reverse=True
            )[:6]

            # Simplify for frontend
            top_out = [{
                "name":         p["name"],
                "short":        p["short"],
                "rating":       p["rating"],
                "goals":        p["goals"],
                "assists":      p["assists"],
                "yellow_cards": p["yellow_cards"],
                "red_cards":    p["red_cards"],
                "own_goals":    player_stats.get(p["name"], {}).get("own_goals", 0),
            } for p in top_players]

            tid = team.get("id", "")
            result.append({
                "team_id":    tid,
                "team_name":  team.get("displayName", ""),
                "team_abbr":  team.get("abbreviation", ""),
                "team_logo":  team.get("logo", ""),
                "formation":  t.get("formation", ""),
                "starters":   starters,
                "bench":      bench,
                "top_players": top_out,
            })

        cache_set(f"lineup_{mid}", result)
        return result
    except Exception as e:
        raise HTTPException(502, str(e))

@app.get("/api/match/{mid}/bxdebug")
async def bx_debug(mid: str):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://site.api.espn.com/apis/site/v2/sports/soccer/FIFA.World/summary",
            params={"event": mid}
        )
        r.raise_for_status()
        data = r.json()
    bx = data.get("boxscore", {}).get("players", [])
    out = []
    for bp in bx:
        tid = bp.get("team", {}).get("id", "")
        tname = bp.get("team", {}).get("displayName", "")
        for sg in bp.get("statistics", []):
            names = sg.get("names", [])
            keys  = sg.get("keys", [])
            labels= sg.get("labels", [])
            sample = []
            for ad in sg.get("athletes", [])[:3]:
                sample.append({
                    "name": ad.get("athlete", {}).get("displayName",""),
                    "starter": ad.get("starter"),
                    "stats": ad.get("stats", []),
                })
            out.append({"team": tname, "tid": tid, "names": names,
                        "keys": keys, "labels": labels, "sample": sample})
    return out

@app.get("/api/standings")
async def get_standings():
    cached = cache_get("standings")
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(ESPN_STANDINGS)
            r.raise_for_status()
            data = r.json()
        cache_set("standings", data)
        return data
    except Exception as e:
        raise HTTPException(502, str(e))

# ── Archivos estáticos ─────────────────────────────────────────────────────

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")
