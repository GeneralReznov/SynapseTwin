"""Enterprise team-analytics routes (manager/admin only)."""
from fastapi import APIRouter, Depends
from app.middleware.auth import require_manager
from app.db.neo4j_db import run_query
from app.services.sarvam import chat_completion

router = APIRouter(prefix="/api/enterprise", tags=["enterprise"])


async def _get_team_analytics(team_id: str, days: int) -> dict:
    rows = await run_query(
        """MATCH (u:User {teamId:$teamId})-[:LOGGED]->(m:Mood)
           WHERE m.date >= date() - duration({days:$days})
           OPTIONAL MATCH (u)-[:SLEPT]->(s:Sleep {date:m.date})
           OPTIONAL MATCH (u)-[:FOCUSED]->(f:FocusSession {date:m.date})
           OPTIONAL MATCH (u)-[:HAS_BURNOUT_RISK]->(b:BurnoutRisk {date:m.date})
           RETURN avg(m.score)   AS avgMoodScore,
                  avg(s.hours)   AS avgSleepHours,
                  avg(f.hours)   AS avgFocusHours,
                  avg(b.score)   AS avgBurnoutScore,
                  count(DISTINCT u) AS activeMembers""",
        {"teamId": team_id, "days": days},
    )
    return rows[0] if rows else {
        "avgMoodScore": 0, "avgSleepHours": 0,
        "avgFocusHours": 0, "avgBurnoutScore": 0, "activeMembers": 0,
    }


async def _get_anonymized_risk(team_id: str, days: int) -> list:
    rows = await run_query(
        """MATCH (u:User {teamId:$teamId})-[:HAS_BURNOUT_RISK]->(b:BurnoutRisk)
           WHERE b.date >= date() - duration({days:$days}) AND b.score >= 60
           RETURN b.score AS score, toString(b.date) AS date, 'anonymous' AS memberId
           ORDER BY b.date DESC LIMIT 20""",
        {"teamId": team_id, "days": days},
    )
    return rows


def _compute_team_health(analytics: dict) -> int:
    mood    = float(analytics.get("avgMoodScore") or 5)
    sleep   = float(analytics.get("avgSleepHours") or 6)
    burnout = float(analytics.get("avgBurnoutScore") or 30)
    score = (mood / 10 * 40) + (min(sleep, 8) / 8 * 30) + ((100 - burnout) / 100 * 30)
    return round(score)


@router.get("/team/{team_id}")
async def team_dashboard(team_id: str, days: int = 14, current_user: dict = Depends(require_manager)):
    analytics = await _get_team_analytics(team_id, days)
    risk      = await _get_anonymized_risk(team_id, days)
    health    = _compute_team_health(analytics)

    recs = []
    burnout_avg = float(analytics.get("avgBurnoutScore") or 0)
    if burnout_avg >= 60:
        recs.append("Introduce a team wellness day or reduced-meeting week.")
    sleep_avg = float(analytics.get("avgSleepHours") or 0)
    if sleep_avg < 6.5:
        recs.append("Survey team for after-hours work pressure.")
    if not recs:
        recs.append("Team metrics are healthy. Maintain current practices.")

    return {
        "teamId":          team_id,
        "days":            days,
        "analytics":       analytics,
        "anonymizedRisk":  risk,
        "teamHealthScore": health,
        "recommendations": recs,
        "generatedAt":     __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "privacyNote":     "Individual journals are never included. Only aggregated, anonymized metrics.",
    }


@router.get("/summary/{team_id}")
async def team_summary(team_id: str, days: int = 7, current_user: dict = Depends(require_manager)):
    analytics = await _get_team_analytics(team_id, days)
    dept_rows = await run_query(
        """MATCH (u:User {teamId:$teamId})-[:HAS_BURNOUT_RISK]->(b:BurnoutRisk)
           WHERE b.date >= date() - duration({days:$days})
           RETURN u.department AS department, avg(b.score) AS avgBurnout, count(u) AS count
           ORDER BY avgBurnout DESC""",
        {"teamId": team_id, "days": days},
    )
    return {
        "teamId":          team_id,
        "days":            days,
        "teamHealthScore": _compute_team_health(analytics),
        "analytics":       analytics,
        "departments":     dept_rows,
        "generatedAt":     __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
