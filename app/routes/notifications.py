"""Smart notification/alert routes."""
from fastapi import APIRouter, Depends
from app.middleware.auth import require_auth
from app.services.neo4j_service import get_weekly_insights
from app.db.neo4j_db import run_query

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/")
async def get_notifications(current_user: dict = Depends(require_auth)):
    user_id = current_user["userId"]
    alerts  = []

    try:
        data    = await get_weekly_insights(user_id)
        weekly  = data.get("weeklyData", [])

        if weekly:
            burnout_avg = sum(d.get("burnoutScore", 0) for d in weekly) / len(weekly)
            if burnout_avg >= 70:
                alerts.append({
                    "type":     "burnout",
                    "severity": "critical",
                    "title":    "⚠️ High Burnout Risk",
                    "message":  f"Your burnout score averaged {burnout_avg:.0f}/100 this week. Please prioritise rest.",
                })
            elif burnout_avg >= 45:
                alerts.append({
                    "type":     "burnout",
                    "severity": "warning",
                    "title":    "🔥 Elevated Stress Detected",
                    "message":  "Moderate burnout signals this week. Consider lighter workload.",
                })

            sleep_avg = sum(d.get("sleepHours", 0) for d in weekly) / len(weekly)
            if sleep_avg < 6.0:
                alerts.append({
                    "type":     "sleep",
                    "severity": "warning",
                    "title":    "😴 Sleep Deficit",
                    "message":  f"Average sleep this week: {sleep_avg:.1f}h. Aim for 7-8h nightly.",
                })

        # Habit streaks — WITH DISTINCT h so one row per habit node, not per TRACKED edge
        habit_rows = await run_query(
            """MATCH (u:User {id:$userId})-[:TRACKED]->(h:Habit)
               WITH DISTINCT h
               WHERE h.streak >= 7
               RETURN h.name AS name, h.streak AS streak
               ORDER BY h.streak DESC LIMIT 5""",
            {"userId": user_id},
        )
        for h in habit_rows:
            alerts.append({
                "type":     "streak",
                "severity": "info",
                "title":    f"🔥 {h['name']} — {h['streak']}-day streak!",
                "message":  "Keep it up! Consistency is the key to lasting change.",
            })

    except Exception as exc:
        alerts.append({
            "type":     "system",
            "severity": "info",
            "title":    "SynapseTwin is warming up",
            "message":  "Start logging your day to receive personalised insights.",
        })

    return {"alerts": alerts, "count": len(alerts)}
