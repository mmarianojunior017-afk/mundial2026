"""
Mundial 2026 - Backend API
FastAPI + API-Football integration
Deploy en Render.com: https://render.com
"""

import os
import time
import asyncio
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

# ══════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════
API_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_BASE = "https://v3.football.api-sports.io"
WC_LEAGUE = 1       # FIFA World Cup en API-Football
WC_SEASON = 2026

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
}

app = FastAPI(title="Mundial 2026 API", version="1.0.0")

# CORS — permite llamadas desde cualquier origen (el propio HTML)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════
# CACHE IN-MEMORY
# TTL diferenciado según el estado del partido:
#   finished  → 24h  (no cambia más)
#   live      → 2 min
#   upcoming  → 30 min
#   standings → 5 min (durante días de partido)
# ══════════════════════════════════════════
class Cache:
    def __init__(self):
        self._store: dict = {}

    def get(self, key: str):
        if key in self._store:
            data, expires = self._store[key]
            if time.time() < expires:
                return data
            del self._store[key]
        return None

    def set(self, key: str, data, ttl_seconds: int):
        self._store[key] = (data, time.time() + ttl_seconds)

    def invalidate(self, key: str):
        self._store.pop(key, None)

cache = Cache()

# ══════════════════════════════════════════
# HELPER: llamada a API-Football
# ══════════════════════════════════════════
async def api_get(endpoint: str, params: dict = None) -> dict:
    """Llama a API-Football y devuelve response['response']."""
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_FOOTBALL_KEY no configurada")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{API_BASE}/{endpoint}", headers=HEADERS, params=params or {})
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise HTTPException(status_code=502, detail=str(data["errors"]))
        return data.get("response", [])


def _match_ttl(status_short: str) -> int:
    """TTL en segundos según estado del partido."""
    if status_short in ("FT", "AET", "PEN", "AWD", "WO"):
        return 86400   # 24h — terminado, no cambia
    if status_short in ("1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"):
        return 120     # 2 min — EN VIVO
    return 1800        # 30 min — próximo o no iniciado


# ══════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════

@app.get("/api/health")
async def health():
    """Healthcheck para Render."""
    return {"status": "ok", "api_key_set": bool(API_KEY)}


# ──────────────────────────────────────────
# 1. TODOS LOS FIXTURES (calendario completo)
# ──────────────────────────────────────────
@app.get("/api/matches")
async def get_all_matches():
    """
    Devuelve todos los partidos del Mundial 2026 con resultado y estado.
    Cache: 10 min (se refresca frecuente para detectar partidos nuevos que terminaron).
    """
    key = "all_matches"
    cached = cache.get(key)
    if cached:
        return cached

    fixtures = await api_get("fixtures", {"league": WC_LEAGUE, "season": WC_SEASON})

    matches = []
    for f in fixtures:
        fix = f["fixture"]
        teams = f["teams"]
        goals = f["goals"]
        score = f["score"]
        league_round = f["league"].get("round", "")

        matches.append({
            "id": fix["id"],
            "date": fix["date"],          # ISO 8601 con timezone
            "timestamp": fix["timestamp"],
            "timezone": fix["timezone"],
            "status": {
                "long": fix["status"]["long"],
                "short": fix["status"]["short"],
                "elapsed": fix["status"].get("elapsed"),
            },
            "round": league_round,
            "venue": {
                "name": fix["venue"]["name"] if fix["venue"] else None,
                "city": fix["venue"]["city"] if fix["venue"] else None,
            },
            "home": {
                "id": teams["home"]["id"],
                "name": teams["home"]["name"],
                "logo": teams["home"]["logo"],
                "winner": teams["home"]["winner"],
            },
            "away": {
                "id": teams["away"]["id"],
                "name": teams["away"]["name"],
                "logo": teams["away"]["logo"],
                "winner": teams["away"]["winner"],
            },
            "score": {
                "halftime": score["halftime"],
                "fulltime": score["fulltime"],
                "extratime": score["extratime"],
                "penalty": score["penalty"],
            },
            "goals": {
                "home": goals["home"],
                "away": goals["away"],
            },
        })

    result = {"count": len(matches), "matches": matches}
    cache.set(key, result, ttl_seconds=600)   # 10 min
    return result


# ──────────────────────────────────────────
# 2. DETALLE DE UN PARTIDO (eventos + stats)
# ──────────────────────────────────────────
@app.get("/api/match/{fixture_id}")
async def get_match_detail(fixture_id: int):
    """
    Devuelve:
    - Info del partido (resultado, estado, minuto)
    - Eventos: goles, tarjetas, sustituciones con jugador + minuto
    - Estadísticas: posesión, tiros, pases, faltas, corners, offside, etc.
    - Alineaciones: 11 titular + suplentes con número de camiseta
    TTL depende del estado del partido.
    """
    key = f"match_{fixture_id}"
    cached = cache.get(key)
    if cached:
        return cached

    # Llamadas en paralelo para minimizar tiempo y uso de requests
    fix_task = api_get("fixtures", {"id": fixture_id})
    events_task = api_get("fixtures/events", {"fixture": fixture_id})
    stats_task = api_get("fixtures/statistics", {"fixture": fixture_id})
    lineups_task = api_get("fixtures/lineups", {"fixture": fixture_id})

    fixtures, raw_events, raw_stats, raw_lineups = await asyncio.gather(
        fix_task, events_task, stats_task, lineups_task
    )

    if not fixtures:
        raise HTTPException(status_code=404, detail="Partido no encontrado")

    f = fixtures[0]
    fix = f["fixture"]
    teams = f["teams"]
    goals = f["goals"]
    score = f["score"]
    status_short = fix["status"]["short"]

    # ── EVENTOS ──────────────────────────────
    events = []
    for ev in raw_events:
        events.append({
            "time": ev["time"]["elapsed"],
            "extra_time": ev["time"].get("extra"),
            "team": ev["team"]["name"],
            "team_id": ev["team"]["id"],
            "player": ev["player"]["name"] if ev["player"] else None,
            "player_id": ev["player"]["id"] if ev["player"] else None,
            "assist": ev["assist"]["name"] if ev.get("assist") and ev["assist"] else None,
            "type": ev["type"],       # "Goal", "Card", "subst", "Var"
            "detail": ev["detail"],   # "Normal Goal", "Yellow Card", "Red Card", "Penalty", etc.
            "comments": ev.get("comments"),
        })

    # ── ESTADÍSTICAS ─────────────────────────
    stats = {}
    for team_stats in raw_stats:
        team_name = team_stats["team"]["name"]
        team_id = team_stats["team"]["id"]
        s = {}
        for stat in team_stats["statistics"]:
            s[stat["type"]] = stat["value"]
        stats[str(team_id)] = {"team": team_name, "stats": s}

    # ── ALINEACIONES ─────────────────────────
    lineups = {}
    for lineup in raw_lineups:
        team_id = lineup["team"]["id"]
        team_name = lineup["team"]["name"]
        formation = lineup.get("formation", "")
        starting = []
        for p in lineup.get("startXI", []):
            pl = p["player"]
            starting.append({
                "id": pl["id"],
                "name": pl["name"],
                "number": pl["number"],
                "pos": pl["pos"],
                "grid": pl.get("grid"),
            })
        subs = []
        for p in lineup.get("substitutes", []):
            pl = p["player"]
            subs.append({
                "id": pl["id"],
                "name": pl["name"],
                "number": pl["number"],
                "pos": pl["pos"],
            })
        lineups[str(team_id)] = {
            "team": team_name,
            "formation": formation,
            "starting_xi": starting,
            "substitutes": subs,
            "coach": lineup.get("coach", {}).get("name"),
        }

    # ── CONTEOS RÁPIDOS DE GOLES/TARJETAS ────
    home_id = teams["home"]["id"]
    away_id = teams["away"]["id"]

    def count_events(team_id, ev_type, ev_detail=None):
        return [
            e for e in events
            if e["team_id"] == team_id
            and e["type"] == ev_type
            and (ev_detail is None or e["detail"] == ev_detail)
        ]

    summary = {
        "home": {
            "goals": count_events(home_id, "Goal"),
            "yellow_cards": count_events(home_id, "Card", "Yellow Card"),
            "red_cards": count_events(home_id, "Card", "Red Card"),
            "yellow_red_cards": count_events(home_id, "Card", "Yellow Red Card"),
        },
        "away": {
            "goals": count_events(away_id, "Goal"),
            "yellow_cards": count_events(away_id, "Card", "Yellow Card"),
            "red_cards": count_events(away_id, "Card", "Red Card"),
            "yellow_red_cards": count_events(away_id, "Card", "Yellow Red Card"),
        },
    }

    result = {
        "id": fix["id"],
        "date": fix["date"],
        "status": {
            "long": fix["status"]["long"],
            "short": status_short,
            "elapsed": fix["status"].get("elapsed"),
        },
        "venue": {
            "name": fix["venue"]["name"] if fix["venue"] else None,
            "city": fix["venue"]["city"] if fix["venue"] else None,
        },
        "home": {
            "id": home_id,
            "name": teams["home"]["name"],
            "logo": teams["home"]["logo"],
        },
        "away": {
            "id": away_id,
            "name": teams["away"]["name"],
            "logo": teams["away"]["logo"],
        },
        "goals": {"home": goals["home"], "away": goals["away"]},
        "score": score,
        "summary": summary,
        "events": events,
        "statistics": stats,
        "lineups": lineups,
    }

    cache.set(key, result, ttl_seconds=_match_ttl(status_short))
    return result


# ──────────────────────────────────────────
# 3. TABLA DE POSICIONES (grupos)
# ──────────────────────────────────────────
@app.get("/api/standings")
async def get_standings():
    """Tabla de posiciones de todos los grupos del Mundial 2026."""
    key = "standings"
    cached = cache.get(key)
    if cached:
        return cached

    raw = await api_get("standings", {"league": WC_LEAGUE, "season": WC_SEASON})
    if not raw:
        raise HTTPException(status_code=404, detail="Standings no disponibles aún")

    groups = []
    for group_data in raw[0]["league"]["standings"]:
        group = []
        for entry in group_data:
            group.append({
                "rank": entry["rank"],
                "team": {
                    "id": entry["team"]["id"],
                    "name": entry["team"]["name"],
                    "logo": entry["team"]["logo"],
                },
                "played": entry["all"]["played"],
                "win": entry["all"]["win"],
                "draw": entry["all"]["draw"],
                "lose": entry["all"]["lose"],
                "goals_for": entry["all"]["goals"]["for"],
                "goals_against": entry["all"]["goals"]["against"],
                "goal_diff": entry["goalsDiff"],
                "points": entry["points"],
                "form": entry.get("form", ""),
                "description": entry.get("description", ""),  # "Qualify" etc.
                "group": entry.get("group", ""),
            })
        if group:
            groups.append({
                "group_name": group[0]["group"],
                "teams": group,
            })

    result = {"groups": groups}
    cache.set(key, result, ttl_seconds=300)   # 5 min
    return result


# ──────────────────────────────────────────
# 4. MÉXICO — partidos + stats especiales
# ──────────────────────────────────────────
MEXICO_TEAM_ID = 164   # ID de México en API-Football

@app.get("/api/mexico")
async def get_mexico():
    """Todos los partidos de México en el Mundial 2026 con eventos y stats."""
    key = "mexico"
    cached = cache.get(key)
    if cached:
        return cached

    fixtures = await api_get(
        "fixtures",
        {"league": WC_LEAGUE, "season": WC_SEASON, "team": MEXICO_TEAM_ID}
    )

    matches = []
    for f in fixtures:
        fix = f["fixture"]
        status_short = fix["status"]["short"]
        match_id = fix["id"]

        entry = {
            "id": match_id,
            "date": fix["date"],
            "status": {
                "long": fix["status"]["long"],
                "short": status_short,
                "elapsed": fix["status"].get("elapsed"),
            },
            "round": f["league"].get("round", ""),
            "venue": {
                "name": fix["venue"]["name"] if fix["venue"] else None,
                "city": fix["venue"]["city"] if fix["venue"] else None,
            },
            "home": {
                "id": f["teams"]["home"]["id"],
                "name": f["teams"]["home"]["name"],
                "logo": f["teams"]["home"]["logo"],
            },
            "away": {
                "id": f["teams"]["away"]["id"],
                "name": f["teams"]["away"]["name"],
                "logo": f["teams"]["away"]["logo"],
            },
            "goals": {
                "home": f["goals"]["home"],
                "away": f["goals"]["away"],
            },
            "events": [],
            "statistics": {},
        }

        # Para partidos que ya iniciaron, traer eventos y stats
        played_statuses = {"FT", "AET", "PEN", "1H", "HT", "2H", "ET", "BT", "P", "LIVE"}
        if status_short in played_statuses:
            ev_key = f"mx_events_{match_id}"
            ev_cached = cache.get(ev_key)
            if ev_cached:
                entry["events"] = ev_cached["events"]
                entry["statistics"] = ev_cached["stats"]
            else:
                ev_raw, st_raw = await asyncio.gather(
                    api_get("fixtures/events", {"fixture": match_id}),
                    api_get("fixtures/statistics", {"fixture": match_id}),
                )
                evs = []
                for ev in ev_raw:
                    evs.append({
                        "time": ev["time"]["elapsed"],
                        "extra_time": ev["time"].get("extra"),
                        "team_id": ev["team"]["id"],
                        "team": ev["team"]["name"],
                        "player": ev["player"]["name"] if ev["player"] else None,
                        "player_id": ev["player"]["id"] if ev["player"] else None,
                        "assist": ev["assist"]["name"] if ev.get("assist") and ev["assist"] else None,
                        "type": ev["type"],
                        "detail": ev["detail"],
                    })
                stats = {}
                for team_stats in st_raw:
                    tid = str(team_stats["team"]["id"])
                    s = {}
                    for stat in team_stats["statistics"]:
                        s[stat["type"]] = stat["value"]
                    stats[tid] = {"team": team_stats["team"]["name"], "stats": s}

                entry["events"] = evs
                entry["statistics"] = stats
                ttl = _match_ttl(status_short)
                cache.set(ev_key, {"events": evs, "stats": stats}, ttl_seconds=ttl)

        matches.append(entry)

    result = {"team_id": MEXICO_TEAM_ID, "matches": matches}
    cache.set(key, result, ttl_seconds=300)
    return result


# ──────────────────────────────────────────
# 5. SEMIFINALES — fixtures #101 y #102
# ──────────────────────────────────────────
@app.get("/api/semifinals")
async def get_semifinals():
    """
    Devuelve los dos partidos de semifinales con detalle completo.
    Usa la ronda 'Semi-finals' para identificarlos.
    """
    key = "semifinals"
    cached = cache.get(key)
    if cached:
        return cached

    all_fixtures = await api_get("fixtures", {"league": WC_LEAGUE, "season": WC_SEASON, "round": "Semi-finals"})

    semis = []
    for f in all_fixtures:
        fix = f["fixture"]
        match_id = fix["id"]
        status_short = fix["status"]["short"]

        item = {
            "id": match_id,
            "date": fix["date"],
            "status": {
                "long": fix["status"]["long"],
                "short": status_short,
                "elapsed": fix["status"].get("elapsed"),
            },
            "venue": {
                "name": fix["venue"]["name"] if fix["venue"] else None,
                "city": fix["venue"]["city"] if fix["venue"] else None,
            },
            "home": {
                "id": f["teams"]["home"]["id"],
                "name": f["teams"]["home"]["name"],
                "logo": f["teams"]["home"]["logo"],
            },
            "away": {
                "id": f["teams"]["away"]["id"],
                "name": f["teams"]["away"]["name"],
                "logo": f["teams"]["away"]["logo"],
            },
            "goals": {
                "home": f["goals"]["home"],
                "away": f["goals"]["away"],
            },
            "score": f["score"],
            "events": [],
            "statistics": {},
        }

        played_statuses = {"FT", "AET", "PEN", "1H", "HT", "2H", "ET", "BT", "P", "LIVE"}
        if status_short in played_statuses:
            ev_raw, st_raw = await asyncio.gather(
                api_get("fixtures/events", {"fixture": match_id}),
                api_get("fixtures/statistics", {"fixture": match_id}),
            )
            item["events"] = [
                {
                    "time": ev["time"]["elapsed"],
                    "extra_time": ev["time"].get("extra"),
                    "team_id": ev["team"]["id"],
                    "team": ev["team"]["name"],
                    "player": ev["player"]["name"] if ev["player"] else None,
                    "type": ev["type"],
                    "detail": ev["detail"],
                }
                for ev in ev_raw
            ]
            stats = {}
            for team_stats in st_raw:
                tid = str(team_stats["team"]["id"])
                s = {stat["type"]: stat["value"] for stat in team_stats["statistics"]}
                stats[tid] = {"team": team_stats["team"]["name"], "stats": s}
            item["statistics"] = stats

        semis.append(item)

    # Ordenar: Dallas primero (Jul 14), Atlanta segundo (Jul 15)
    semis.sort(key=lambda x: x["date"])
    result = {"semifinals": semis}
    cache.set(key, result, ttl_seconds=_match_ttl(semis[0]["status"]["short"] if semis else "NS"))
    return result


# ──────────────────────────────────────────
# STATIC FILES — sirve el HTML frontend
# ──────────────────────────────────────────
# Crea carpeta "static/" y pon mundial2026.html ahí.
# Al hacer el build final, copia el HTML a static/index.html
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")

# ══════════════════════════════════════════
# ENTRY POINT (local dev)
# ══════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
