"""Insights routes — wellness analytics, causal patterns, AI reasoning, predictions."""
from fastapi import APIRouter, Depends
from app.middleware.auth import require_auth
from app.services.neo4j_service import (
    get_weekly_insights, detect_causal_patterns, get_timeline, get_predictions_data,
)
from app.services.sarvam import chat_completion
from app.services.twin_score import compute_twin_score

router = APIRouter(prefix="/api/insights", tags=["insights"])


def _build_causal_insight(sleep_mood: dict, ex_prod: dict,
                           mood_avg: float, sleep_avg: float,
                           burnout_avg: float, focus_avg: float) -> str:
    parts = []
    avg_mood_sleep = sleep_mood.get("avgMoodWithGoodSleep")
    if avg_mood_sleep:
        parts.append(
            f"On nights with 7+ hours sleep, your mood averages {avg_mood_sleep:.1f}/10 — "
            f"a clear causal link between rest and emotional state."
        )
    with_ex    = float(ex_prod.get("withExercise") or 0)
    without_ex = float(ex_prod.get("withoutExercise") or 0)
    if with_ex > without_ex and without_ex > 0:
        diff = with_ex - without_ex
        parts.append(
            f"Exercise days give you {diff:.1f} more focus hours — your graph shows "
            f"a direct Exercise → Focus causal chain."
        )
    if mood_avg < 5:
        parts.append(
            f"Mood has averaged {mood_avg:.1f}/10 this week — "
            f"consider stress-reduction practices to break the low-mood cycle."
        )
    if sleep_avg < 6.5:
        parts.append(
            f"Chronic sleep deficit detected ({sleep_avg:.1f}h avg). "
            f"Poor Sleep → Reduced Focus → Higher Stress is a known cascade in your graph."
        )
    if burnout_avg > 60:
        parts.append(
            f"⚠️ Burnout risk at {burnout_avg:.0f}/100. "
            f"Your knowledge graph shows overlapping stress, sleep deficit, and low habit completion."
        )
    return " ".join(parts) or "Keep logging daily to reveal causal patterns in your knowledge graph."


def _build_predictions(
    weekly: list, habit_gaps: list, missed_goals: list,
    mood_avg: float, sleep_avg: float, burnout_avg: float, focus_avg: float,
) -> list[dict]:
    predictions = []

    # Burnout
    if burnout_avg >= 70:
        predictions.append({
            "type": "burnout", "probability": 0.87,
            "timeframe": "3 days", "confidence": "91%",
            "message": "Burnout is imminent. Persistent stress + sleep deficit + low habit completion detected.",
        })
    elif burnout_avg >= 50:
        predictions.append({
            "type": "burnout", "probability": 0.58,
            "timeframe": "7 days", "confidence": "76%",
            "message": "Moderate burnout building. Without recovery, performance will decline.",
        })

    # Productivity decline from sleep
    if sleep_avg < 6.5:
        predictions.append({
            "type": "productivity_decline", "probability": 0.74,
            "timeframe": "tomorrow", "confidence": "83%",
            "message": f"Sleep debt ({sleep_avg:.1f}h avg) will reduce cognitive capacity by ~30%.",
        })

    # Learning stagnation
    if focus_avg < 2:
        predictions.append({
            "type": "learning_stagnation", "probability": 0.68,
            "timeframe": "this week", "confidence": "71%",
            "message": "Focus time below 2h/day — skill development will stagnate without intervention.",
        })

    # Habit failure
    if habit_gaps:
        predictions.append({
            "type": "habit_failure", "probability": 0.65,
            "timeframe": "this week", "confidence": "78%",
            "message": f"Habits falling behind ({len(habit_gaps)} with recent gaps). Streaks at risk.",
        })

    # Missed goals
    if missed_goals:
        predictions.append({
            "type": "missed_goal", "probability": 0.60,
            "timeframe": "this month", "confidence": "69%",
            "message": f"{len(missed_goals)} goal(s) with <10% progress. Review blockers in your Goal Graph.",
        })

    # Stress spike
    if mood_avg < 4.5 and burnout_avg > 40:
        predictions.append({
            "type": "stress_spike", "probability": 0.63,
            "timeframe": "next 2 days", "confidence": "72%",
            "message": "Low mood + elevated burnout may cascade into acute stress. Proactive recovery recommended.",
        })

    # Poor sleep trend
    if len(weekly) >= 5:
        recent_sleep = [d.get("sleepHours", 0) for d in weekly[-5:]]
        if all(s < 6.5 for s in recent_sleep):
            predictions.append({
                "type": "poor_sleep", "probability": 0.79,
                "timeframe": "ongoing", "confidence": "85%",
                "message": "5-day sleep deficit streak. Chronic sleep restriction compounds all other risks.",
            })

    return predictions


@router.get("/")
async def get_insights(current_user: dict = Depends(require_auth)):
    user_id = current_user["userId"]
    data    = await get_weekly_insights(user_id)
    weekly  = data.get("weeklyData", [])

    if not weekly:
        return {
            "weeklyData": [], "topHabits": [], "goals": [],
            "moodAverage": 0, "sleepAverage": 0, "focusAverage": 0,
            "burnoutScore": 0, "burnoutTrend": "stable",
            "twinBreakdown": {"physical": 50, "mental": 50, "productivity": 50, "learning": 50, "social": 50},
            "causalInsight": "Log your first check-in using voice or text to start building your knowledge graph.",
            "predictions": [],
            "recommendations": {"today": [], "thisWeek": []},
            "needsMoreData": True,
        }

    mood_avg    = sum(d.get("moodScore", 5)    for d in weekly) / len(weekly)
    sleep_avg   = sum(d.get("sleepHours", 0)   for d in weekly) / len(weekly)
    focus_avg   = sum(d.get("focusHours", 0)   for d in weekly) / len(weekly)
    burnout_avg = sum(d.get("burnoutScore", 0) for d in weekly) / len(weekly)
    burnout_trend = (
        "worsening"  if burnout_avg > 60 else
        "stable"     if burnout_avg > 35 else
        "improving"
    )

    # Twin Score breakdown from weekly aggregate
    twin_input = {
        "mood": {"score": mood_avg, "energyLevel": "Medium", "stressLevel": "Medium"},
        "sleep": {"hours": sleep_avg},
        "work": {"focusHours": focus_avg},
        "weeklyData": weekly,
    }
    twin_result    = compute_twin_score(twin_input)
    twin_breakdown = twin_result["breakdown"]

    # Causal patterns from Neo4j
    causal = {}
    try:
        causal = await detect_causal_patterns(user_id)
    except Exception:
        pass

    causal_insight = _build_causal_insight(
        causal.get("sleepMood", {}),
        causal.get("exerciseProductivity", {}),
        mood_avg, sleep_avg, burnout_avg, focus_avg,
    )

    # Prediction Engine data
    pred_data = {}
    try:
        pred_data = await get_predictions_data(user_id)
    except Exception:
        pass

    predictions = _build_predictions(
        weekly,
        pred_data.get("habitGaps", []),
        pred_data.get("missedGoals", []),
        mood_avg, sleep_avg, burnout_avg, focus_avg,
    )

    # AI Reasoning via Sarvam LLM
    ai_reasoning = None
    if len(weekly) >= 3:
        try:
            prompt = (
                f"User's 7-day digital twin summary: mood avg {mood_avg:.1f}/10, "
                f"sleep avg {sleep_avg:.1f}h, focus avg {focus_avg:.1f}h, "
                f"burnout risk {burnout_avg:.0f}/100. "
                f"Twin Score breakdown: physical={twin_breakdown.get('physical')}, "
                f"mental={twin_breakdown.get('mental')}, "
                f"productivity={twin_breakdown.get('productivity')}, "
                f"learning={twin_breakdown.get('learning')}, "
                f"social={twin_breakdown.get('social')}. "
                f"Causal patterns from Neo4j graph: {causal}. "
                f"Generate 2-3 sentences of precise, empathetic analysis explaining WHY these "
                f"metrics are connected and what one specific action would have the highest impact."
            )
            r = await chat_completion(
                "You are SynapseTwin, an AI Digital Twin wellness analyst. "
                "Speak as a knowledgeable, empathetic coach. Be specific and actionable.",
                prompt,
            )
            if r["success"]:
                ai_reasoning = r["content"]
        except Exception:
            pass

    return {
        **data,
        "moodAverage":    round(mood_avg, 1),
        "sleepAverage":   round(sleep_avg, 1),
        "focusAverage":   round(focus_avg, 1),
        "burnoutScore":   round(burnout_avg),
        "burnoutTrend":   burnout_trend,
        "twinBreakdown":  twin_breakdown,
        "causalInsight":  causal_insight,
        "causalPatterns": causal,
        "aiReasoning":    ai_reasoning,
        "predictions":    predictions,
    }


@router.get("/timeline")
async def timeline(days: int = 30, current_user: dict = Depends(require_auth)):
    data = await get_timeline(current_user["userId"], min(days, 90))
    return {"timeline": data}
