"""Environment routes — OpenWeather, Location, and productivity impact analysis."""
from __future__ import annotations
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx

from app.middleware.auth import require_auth
from app.services.sarvam import chat_completion as sarvam_chat
from app.db.neo4j_db import run_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/environment", tags=["environment"])

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
OPENWEATHER_BASE    = "https://api.openweathermap.org/data/2.5"


# ── Models ─────────────────────────────────────────────────────────────────────

class LocationBody(BaseModel):
    latitude: float
    longitude: float
    location_name: Optional[str] = None
    location_type: Optional[str] = "home"   # home | office | commute | other


class WeatherBody(BaseModel):
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# ── AI helper ─────────────────────────────────────────────────────────────────

async def _ai(system: str, user: str, json_mode: bool = False) -> dict:
    """Sarvam AI is primary; automatically falls back to Groq internally if Sarvam is unavailable."""
    return await sarvam_chat(system, user, json_mode=json_mode)


# ── Weather ───────────────────────────────────────────────────────────────────

@router.get("/weather")
async def get_weather(
    city: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    current_user: dict = Depends(require_auth),
):
    """Fetch current weather by city name or coordinates, with AI productivity impact analysis."""
    if not OPENWEATHER_API_KEY:
        return _mock_weather()

    try:
        # Build OpenWeather query
        if lat is not None and lon is not None:
            url = f"{OPENWEATHER_BASE}/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
        elif city:
            url = f"{OPENWEATHER_BASE}/weather?q={city}&appid={OPENWEATHER_API_KEY}&units=metric"
        else:
            return {"success": False, "error": "Provide city or coordinates"}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.json()

        weather_data = _parse_weather(raw)

        # AI productivity impact
        impact = await _analyze_weather_impact(weather_data)
        weather_data["productivityImpact"] = impact
        weather_data["success"] = True

        # Log to Neo4j
        try:
            await _log_weather_neo4j(current_user["userId"], weather_data)
        except Exception as e:
            logger.warning(f"Weather Neo4j log failed: {e}")

        return weather_data

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="City not found. Try a different name.")
        logger.error(f"OpenWeather HTTP error: {e}")
        return _mock_weather()
    except Exception as exc:
        logger.error(f"Weather fetch error: {exc}")
        return _mock_weather()


@router.get("/forecast")
async def get_forecast(
    city: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    current_user: dict = Depends(require_auth),
):
    """5-day weather forecast with daily summaries."""
    if not OPENWEATHER_API_KEY:
        return {"success": True, "forecast": _mock_forecast(), "mock": True}

    try:
        if lat is not None and lon is not None:
            url = f"{OPENWEATHER_BASE}/forecast?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&cnt=40"
        elif city:
            url = f"{OPENWEATHER_BASE}/forecast?q={city}&appid={OPENWEATHER_API_KEY}&units=metric&cnt=40"
        else:
            return {"success": False, "error": "Provide city or coordinates"}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.json()

        # Group by day
        days: dict[str, list] = {}
        for item in raw.get("list", []):
            day = item["dt_txt"][:10]
            days.setdefault(day, []).append(item)

        forecast = []
        for day, items in list(days.items())[:5]:
            temps   = [i["main"]["temp"] for i in items]
            desc    = items[len(items)//2]["weather"][0]["description"]
            icon    = items[len(items)//2]["weather"][0]["icon"]
            humidity = items[0]["main"]["humidity"]
            forecast.append({
                "date":      day,
                "tempMin":   round(min(temps), 1),
                "tempMax":   round(max(temps), 1),
                "tempAvg":   round(sum(temps)/len(temps), 1),
                "description": desc.title(),
                "icon":      f"https://openweathermap.org/img/wn/{icon}@2x.png",
                "humidity":  humidity,
                "productivityScore": _weather_productivity_score(desc, sum(temps)/len(temps), humidity),
            })

        return {"success": True, "city": raw.get("city", {}).get("name", city), "forecast": forecast}

    except Exception as exc:
        logger.error(f"Forecast error: {exc}")
        return {"success": True, "forecast": _mock_forecast(), "mock": True}


@router.post("/location")
async def log_location(body: LocationBody, current_user: dict = Depends(require_auth)):
    """Log user's location and correlate with productivity patterns."""
    user_id  = current_user["userId"]
    now      = datetime.now(timezone.utc).isoformat()

    # Fetch weather for this location if API key available (fetched before the
    # Neo4j write so we can persist a full snapshot on the Location node —
    # this is what powers both the location history list and the map pins).
    weather_data = {}
    if OPENWEATHER_API_KEY:
        try:
            url = f"{OPENWEATHER_BASE}/weather?lat={body.latitude}&lon={body.longitude}&appid={OPENWEATHER_API_KEY}&units=metric"
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                weather_data = _parse_weather(resp.json())
        except Exception:
            pass

    try:
        await run_query(
            """MERGE (u:User {id:$userId})
               CREATE (l:Location {
                   lat: $lat, lon: $lon, name: $name,
                   locationType: $type, timestamp: $ts,
                   weatherCondition: $weatherCondition,
                   temperature: $temperature,
                   productivityScore: $productivityScore
               })
               CREATE (u)-[:LOCATED_AT]->(l)""",
            {
                "userId": user_id, "lat": body.latitude, "lon": body.longitude,
                "name":   body.location_name or "Unknown",
                "type":   body.location_type, "ts": now,
                "weatherCondition": weather_data.get("description", ""),
                "temperature":      weather_data.get("temp"),
                "productivityScore": weather_data.get("productivityScore"),
            },
        )
    except Exception as e:
        logger.warning(f"Location Neo4j log failed: {e}")

    # AI insight on location productivity
    insight = await _analyze_location_productivity(
        body.location_type,
        body.location_name or "your location",
        weather_data,
    )

    return {
        "success": True,
        "location": {
            "lat":  body.latitude, "lon": body.longitude,
            "name": body.location_name, "type": body.location_type,
        },
        "weather":  weather_data or None,
        "insight":  insight,
        "loggedAt": now,
    }


@router.get("/history")
async def get_environment_history(
    limit: int = 7,
    current_user: dict = Depends(require_auth),
):
    """Get recent logged locations (with coordinates + weather snapshot) for the user —
    powers both the location history list and the live map's historical pins."""
    try:
        rows = await run_query(
            """MATCH (u:User {id:$userId})-[:LOCATED_AT]->(l:Location)
               RETURN l.name AS location_name, l.locationType AS location_type,
                      l.lat AS lat, l.lon AS lon,
                      l.weatherCondition AS weather_condition,
                      l.temperature AS temperature,
                      l.productivityScore AS productivity_score,
                      l.timestamp AS logged_at
               ORDER BY l.timestamp DESC LIMIT $limit""",
            {"userId": current_user["userId"], "limit": limit},
        )
        return {"success": True, "history": rows}
    except Exception as e:
        logger.error(f"Environment history error: {e}")
        return {"success": True, "history": []}


@router.get("/impact-analysis")
async def get_environment_impact(current_user: dict = Depends(require_auth)):
    """AI analysis of how environment (weather/location) correlates with user's productivity."""
    user_id = current_user["userId"]

    # Get recent environment logs
    weather_history = []
    try:
        weather_history = await run_query(
            """MATCH (u:User {id:$userId})-[:WEATHER_LOG]->(w:WeatherLog)
               RETURN w.temp AS temp, w.description AS description,
                      w.productivityScore AS productivityScore, w.timestamp AS timestamp
               ORDER BY w.timestamp DESC LIMIT 14""",
            {"userId": user_id},
        )
    except Exception:
        pass

    # Get recent productivity from daily logs
    productivity_logs = []
    try:
        productivity_logs = await run_query(
            """MATCH (u:User {id:$userId})-[:LOGGED]->(d:DailyLog)
               RETURN d.focusHours AS focusHours, d.moodScore AS moodScore, d.date AS date
               ORDER BY d.date DESC LIMIT 14""",
            {"userId": user_id},
        )
    except Exception:
        pass

    if not weather_history and not productivity_logs:
        return {
            "success": True,
            "analysis": "Start logging your location and weather data to unlock environment-productivity correlation insights. Once you have 7+ days of data, your Digital Twin will identify patterns like 'Sunny days → +35% focus hours'.",
            "correlations": [],
            "recommendations": [],
        }

    system = (
        "You are SynapseTwin, an AI Digital Twin analyst specializing in environment-productivity correlations. "
        "Analyze the data and return clear, actionable insights. Be specific with numbers when available."
    )

    user_msg = (
        f"Environment history (last 14 entries): {weather_history}\n"
        f"Productivity history (last 14 entries): {productivity_logs}\n\n"
        "Analyze the relationship between weather/environment and productivity. "
        "Provide: 1) Key correlations found, 2) Best conditions for this user's productivity, "
        "3) Specific recommendations. Be concise (3-4 sentences total)."
    )

    result = await _ai(system, user_msg)
    analysis = result.get("content", "Analyzing your environment-productivity patterns…") if result.get("success") else \
               "Log more environment data to see how weather and location affect your productivity."

    return {
        "success": True,
        "analysis": analysis,
        "dataPoints": len(weather_history),
        "correlations": _build_correlations(weather_history, productivity_logs),
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_weather(raw: dict) -> dict:
    main    = raw.get("main", {})
    weather = raw.get("weather", [{}])[0]
    wind    = raw.get("wind", {})
    sys     = raw.get("sys", {})
    desc    = weather.get("description", "")
    icon    = weather.get("icon", "01d")
    temp    = main.get("temp", 20)
    humidity = main.get("humidity", 50)

    return {
        "city":            raw.get("name", ""),
        "country":         sys.get("country", ""),
        "temp":            round(temp, 1),
        "feelsLike":       round(main.get("feels_like", temp), 1),
        "tempMin":         round(main.get("temp_min", temp), 1),
        "tempMax":         round(main.get("temp_max", temp), 1),
        "humidity":        humidity,
        "pressure":        main.get("pressure", 1013),
        "description":     desc.title(),
        "icon":            f"https://openweathermap.org/img/wn/{icon}@2x.png",
        "iconCode":        icon,
        "windSpeed":       round(wind.get("speed", 0), 1),
        "cloudiness":      raw.get("clouds", {}).get("all", 0),
        "visibility":      raw.get("visibility", 10000),
        "sunrise":         sys.get("sunrise", 0),
        "sunset":          sys.get("sunset", 0),
        "timezone":        raw.get("timezone", 0),
        "productivityScore": _weather_productivity_score(desc, temp, humidity),
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }


def _weather_productivity_score(description: str, temp: float, humidity: int) -> int:
    """Heuristic productivity score (0-100) based on weather conditions."""
    score = 70  # baseline
    desc  = description.lower()

    # Temperature sweet spot (18-24°C is optimal for focus)
    if 18 <= temp <= 24:    score += 20
    elif 15 <= temp <= 27:  score += 10
    elif temp < 5 or temp > 35: score -= 20

    # Weather conditions
    if any(w in desc for w in ["clear", "sunny"]):   score += 10
    elif any(w in desc for w in ["overcast", "clouds"]): score -= 5
    elif any(w in desc for w in ["rain", "drizzle"]): score -= 10
    elif any(w in desc for w in ["thunderstorm", "storm"]): score -= 20
    elif "snow" in desc: score -= 15

    # Humidity
    if 40 <= humidity <= 60: score += 5
    elif humidity > 80:      score -= 10

    return max(0, min(100, score))


async def _analyze_weather_impact(weather: dict) -> dict:
    """Use AI to generate productivity impact analysis for current weather."""
    desc  = weather.get("description", "")
    temp  = weather.get("temp", 20)
    score = weather.get("productivityScore", 70)

    # Quick heuristic labels (no AI needed for speed)
    if score >= 80:
        impact_label = "High"
        impact_color = "green"
        tip = "Perfect conditions for deep work. Block 2-4 hour focus sessions today."
    elif score >= 60:
        impact_label = "Medium"
        impact_color = "yellow"
        tip = "Good conditions overall. Take short breaks if you feel discomfort."
    else:
        impact_label = "Low"
        impact_color = "red"
        tip = "Challenging conditions. Work from a comfortable indoor space and stay hydrated."

    return {
        "score":       score,
        "label":       impact_label,
        "color":       impact_color,
        "tip":         tip,
        "bestFor":     _weather_best_for(desc, temp),
    }


def _weather_best_for(description: str, temp: float) -> list[str]:
    desc = description.lower()
    if "clear" in desc or "sunny" in desc:
        return ["Deep focus work", "Creative brainstorming", "Outdoor meetings"]
    elif "rain" in desc or "drizzle" in desc:
        return ["Reading & learning", "Writing & documentation", "Virtual collaboration"]
    elif "cloud" in desc:
        return ["Meetings & collaboration", "Administrative tasks", "Planning sessions"]
    elif "storm" in desc:
        return ["Individual focused work", "Async communication", "Research tasks"]
    elif temp < 10:
        return ["High-energy tasks", "Morning workouts", "Strategic planning"]
    else:
        return ["Balanced work", "Team collaboration", "Problem-solving sessions"]


async def _analyze_location_productivity(
    location_type: str,
    location_name: str,
    weather_data: dict,
) -> str:
    weather_context = f"Weather: {weather_data.get('description', 'unknown')}, {weather_data.get('temp', '?')}°C" if weather_data else "Weather: not available"

    system = "You are SynapseTwin, a productivity AI Digital Twin. Give a brief, actionable insight."
    user_msg = (
        f"User is at: {location_name} (type: {location_type}). {weather_context}. "
        "Give a 1-2 sentence personalized productivity insight for this specific location + conditions combination. "
        "Be practical and specific."
    )

    result = await _ai(system, user_msg)
    if result.get("success"):
        return result["content"]

    defaults = {
        "office": "Office environment detected. Great for structured work and team collaboration.",
        "home":   "Working from home. Minimize distractions and set clear boundaries for focus time.",
        "commute":"In transit. Good time for podcasts, planning, or light reading.",
    }
    return defaults.get(location_type, "Environment logged. Your Digital Twin is building environment-productivity correlations.")


async def _log_weather_neo4j(user_id: str, weather: dict):
    await run_query(
        """MERGE (u:User {id:$userId})
           CREATE (w:WeatherLog {
               city: $city, temp: $temp, description: $description,
               humidity: $humidity, feelsLike: $feelsLike,
               productivityScore: $productivityScore, timestamp: $timestamp
           })
           CREATE (u)-[:WEATHER_LOG]->(w)""",
        {
            "userId":            user_id,
            "city":              weather.get("city", ""),
            "temp":              weather.get("temp", 0),
            "description":       weather.get("description", ""),
            "humidity":          weather.get("humidity", 0),
            "feelsLike":         weather.get("feelsLike", 0),
            "productivityScore": weather.get("productivityScore", 70),
            "timestamp":         weather.get("timestamp", datetime.now(timezone.utc).isoformat()),
        },
    )


def _build_correlations(weather_history: list, productivity_logs: list) -> list[dict]:
    """Simple correlation heuristics from historical data."""
    correlations = []

    if len(weather_history) >= 3 and len(productivity_logs) >= 3:
        high_prod_days  = [p for p in productivity_logs if (p.get("focusHours") or 0) > 4]
        low_prod_days   = [p for p in productivity_logs if (p.get("focusHours") or 0) < 2]

        if high_prod_days:
            correlations.append({
                "type":  "productivity_peak",
                "label": "High Focus Days",
                "value": f"You average {sum(p.get('focusHours',0) for p in high_prod_days)/len(high_prod_days):.1f}h focus on your best days",
            })

        correlations.append({
            "type":  "data_logged",
            "label": "Environment Data Points",
            "value": f"{len(weather_history)} weather entries logged",
        })

    return correlations


def _mock_weather() -> dict:
    """Return a structured mock when no API key is configured."""
    return {
        "success":    True,
        "mock":       True,
        "city":       "Mumbai",
        "country":    "IN",
        "temp":       28.5,
        "feelsLike":  31.0,
        "tempMin":    26.0,
        "tempMax":    30.0,
        "humidity":   75,
        "pressure":   1012,
        "description": "Partly Cloudy",
        "icon":       "https://openweathermap.org/img/wn/02d@2x.png",
        "iconCode":   "02d",
        "windSpeed":  3.5,
        "cloudiness": 40,
        "productivityScore": 65,
        "productivityImpact": {
            "score": 65, "label": "Medium", "color": "yellow",
            "tip": "Moderate conditions — good for collaborative work.",
            "bestFor": ["Meetings", "Administrative tasks", "Planning"],
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "notice":    "Add OPENWEATHER_API_KEY for live weather data.",
    }


def _mock_forecast() -> list:
    from datetime import timedelta
    base = datetime.now(timezone.utc)
    return [
        {
            "date": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
            "tempMin": 22 + i, "tempMax": 30 + i, "tempAvg": 26 + i,
            "description": ["Sunny", "Partly Cloudy", "Cloudy", "Light Rain", "Clear"][i % 5],
            "icon": "https://openweathermap.org/img/wn/02d@2x.png",
            "humidity": 65,
            "productivityScore": [85, 72, 65, 55, 80][i % 5],
        }
        for i in range(5)
    ]
