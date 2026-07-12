"""SynapseTwin Agent Pipeline — 7 Specialized Agents + Input Pre-processors."""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any

from app.services.sarvam import (
    detect_language, translate_text, detect_emotion,
    extract_entities, generate_twin_response, text_to_speech, LANG_NAME,
)
from app.services.neo4j_service import (
    save_daily_log, get_weekly_insights, detect_causal_patterns,
    get_user_history, upsert_goal, save_causal_link,
)
from app.services.twin_score import compute_twin_score

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# INPUT PRE-PROCESSORS  (Language · Emotion · Entity)
# These prepare raw input before the 7 specialized agents run.
# ═══════════════════════════════════════════════════════════════════════════

async def _preprocess_language(ctx: dict) -> dict:
    raw       = ctx["rawInput"]
    preferred = ctx.get("preferredLanguage", "en-IN")

    detected = await detect_language(raw)
    ctx["detectedLanguage"]     = detected.get("languageCode", "en-IN")
    ctx["detectedLanguageName"] = detected.get("languageName", "English")

    english = raw
    if ctx["detectedLanguage"] != "en-IN":
        tr      = await translate_text(raw, ctx["detectedLanguage"], "en-IN")
        english = tr.get("translatedText", raw)

    ctx["englishInput"]     = english
    ctx["responseLanguage"] = preferred or ctx["detectedLanguage"]
    logger.info(f"[Language] detected={ctx['detectedLanguageName']}")
    return ctx


async def _preprocess_emotion(ctx: dict) -> dict:
    emotion = await detect_emotion(ctx["englishInput"])
    ctx["emotion"] = emotion
    emotion_to_score = {
        "happy": 8, "excited": 9, "neutral": 6, "tired": 4,
        "sad": 3,   "stressed": 3, "anxious": 4, "frustrated": 3,
        "calm": 7,  "energized": 8,
    }
    ctx["inferredMoodScore"] = emotion_to_score.get(
        emotion.get("emotion", "neutral"), 5
    )
    logger.info(f"[Emotion] {emotion.get('emotion')} burnout={emotion.get('burnoutSignals')}")
    return ctx


async def _preprocess_entity(ctx: dict) -> dict:
    entities = await extract_entities(ctx["englishInput"])
    if not entities.get("mood"):
        emo = ctx.get("emotion", {})
        entities["mood"] = {
            "score":       ctx.get("inferredMoodScore", 5),
            "energyLevel": "High" if emo.get("intensity", 0.5) > 0.6 else
                           ("Low" if emo.get("intensity", 0.5) < 0.3 else "Medium"),
            "stressLevel": "High" if emo.get("burnoutSignals") else "Medium",
            "notes":       ctx["englishInput"][:300],
        }
    ctx["extractedEntities"] = entities
    logger.info(f"[Entity] keys={list(entities.keys())}")
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 1 — Memory Agent
# Stores everything. Updates Neo4j Knowledge Graph. Creates relationships.
# ═══════════════════════════════════════════════════════════════════════════

async def memory_agent(ctx: dict) -> dict:
    user_id  = ctx.get("userId")
    entities = ctx.get("extractedEntities", {})

    result = compute_twin_score({**entities, "weeklyData": ctx.get("weeklyData", [])})
    ctx.update({
        "twinScore":     result["twinScore"],
        "twinBreakdown": result["breakdown"],
        "burnoutScore":  result["burnoutScore"],
        "twinSummary":   result["summary"],
    })

    if user_id:
        try:
            await save_daily_log(user_id, {
                **entities,
                "burnoutScore": result["burnoutScore"],
                "twinScore":    result["twinScore"],
                "emotion":      ctx.get("emotion", {}),
                "language":     ctx.get("detectedLanguage", "en-IN"),
                "rawInput":     ctx.get("rawInput", ""),
            })
            ctx["memoryUpdated"] = True
            logger.info(f"[MemoryAgent] Knowledge graph updated for {user_id}")
        except Exception as exc:
            logger.warning(f"[MemoryAgent] Neo4j save failed: {exc}")
            ctx["memoryUpdated"] = False
    else:
        ctx["memoryUpdated"] = False

    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 2 — Health Agent
# Analyzes: Sleep · Stress · Exercise · Recovery · Nutrition · Hydration
# ═══════════════════════════════════════════════════════════════════════════

async def health_agent(ctx: dict) -> dict:
    entities = ctx.get("extractedEntities", {})
    sleep    = entities.get("sleep") or {}
    exercise = entities.get("exercise") or {}
    mood     = entities.get("mood") or {}
    water    = float(entities.get("waterIntake") or 0)
    emo      = ctx.get("emotion", {})

    sleep_hours = float(sleep.get("hours") or 0)
    analysis    = []
    alerts      = []
    score       = ctx.get("twinBreakdown", {}).get("physical", 50)

    # Sleep analysis
    if sleep_hours >= 8:
        analysis.append("Excellent sleep — your physical recovery is optimal.")
    elif sleep_hours >= 7:
        analysis.append("Good sleep duration supporting recovery.")
    elif sleep_hours >= 6:
        analysis.append("Slightly below optimal sleep. Aim for 7-8 hours.")
        alerts.append({"type": "sleep_deficit", "severity": "warning", "message": "Sleep under 7h detected"})
    elif sleep_hours > 0:
        analysis.append(f"Sleep deficit detected ({sleep_hours:.1f}h). Performance impact likely.")
        alerts.append({"type": "sleep_deficit", "severity": "critical", "message": f"Only {sleep_hours:.1f}h sleep — rest is critical"})

    # Exercise analysis
    if exercise.get("done"):
        dur = int(exercise.get("durationMinutes") or 0)
        typ = exercise.get("type", "exercise")
        analysis.append(f"{'Great' if dur >= 45 else 'Light'} {typ} session ({dur} min) boosts recovery and mood.")
    else:
        analysis.append("No exercise logged today. Even a 20-min walk improves mental clarity.")

    # Hydration
    if water >= 2.5:
        analysis.append("Good hydration level.")
    elif water > 0:
        analysis.append(f"Low water intake ({water:.1f}L). Aim for 2.5L daily.")

    # Stress/recovery signal
    stress = str((mood.get("stressLevel") or "medium")).lower()
    if stress == "high":
        analysis.append("High stress detected. Recovery activities recommended.")
        alerts.append({"type": "high_stress", "severity": "warning", "message": "Elevated stress level"})

    ctx["healthAnalysis"] = {
        "score":     score,
        "analysis":  analysis,
        "alerts":    alerts,
        "sleepHours": sleep_hours,
        "exercised":  bool(exercise.get("done")),
        "stressLevel": stress,
    }
    logger.info(f"[HealthAgent] score={score} sleep={sleep_hours}h exercise={exercise.get('done')}")
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 3 — Productivity Agent
# Analyzes: Focus Sessions · Tasks · Meetings · Workload · Deadlines
# ═══════════════════════════════════════════════════════════════════════════

async def productivity_agent(ctx: dict) -> dict:
    entities    = ctx.get("extractedEntities", {})
    work        = entities.get("work") or {}
    focus_hours = float(work.get("focusHours") or 0)
    meetings    = int(work.get("meetings") or 0)
    stressful   = bool(work.get("stressful"))
    goals       = entities.get("goals") or []
    user_id     = ctx.get("userId")

    analysis = []
    causal   = {}

    if focus_hours >= 6:
        analysis.append(f"High focus day ({focus_hours:.1f}h). Deep work momentum is strong.")
    elif focus_hours >= 3:
        analysis.append(f"Moderate focus ({focus_hours:.1f}h). Consider blocking distractions tomorrow.")
    elif focus_hours > 0:
        analysis.append(f"Low focus today ({focus_hours:.1f}h). Check for blocking factors.")

    if meetings > 6:
        analysis.append(f"Heavy meeting load ({meetings} meetings) is fragmenting your focus time.")
        causal["meetingOverload"] = True
    elif meetings > 3:
        analysis.append(f"{meetings} meetings today — moderate meeting load.")
    
    if stressful:
        analysis.append("Work was reported as stressful. This may affect tomorrow's performance.")

    # Link workload to goals in Neo4j if overloaded
    if user_id and meetings > 6 and goals:
        for g in goals[:1]:  # link first goal as potentially blocked
            title = g.get("title", "")
            if title:
                try:
                    await save_causal_link(
                        user_id, "WorkloadStress", title,
                        "BLOCKED_BY", {"reason": "high meeting load"}
                    )
                except Exception:
                    pass

    # Retrieve causal patterns for context
    if user_id:
        try:
            causal_data = await detect_causal_patterns(user_id)
            ctx["causalPatterns"] = causal_data
            ex_prod = causal_data.get("exerciseProductivity", {})
            with_ex    = float(ex_prod.get("withExercise") or 0)
            without_ex = float(ex_prod.get("withoutExercise") or 0)
            if with_ex > without_ex and without_ex > 0:
                analysis.append(
                    f"Graph insight: Exercise days give you {with_ex - without_ex:.1f}h more focus."
                )
        except Exception:
            pass

    ctx["productivityAnalysis"] = {
        "score":       ctx.get("twinBreakdown", {}).get("productivity", 50),
        "focusHours":  focus_hours,
        "meetings":    meetings,
        "analysis":    analysis,
        "causal":      causal,
    }
    logger.info(f"[ProductivityAgent] focus={focus_hours}h meetings={meetings}")
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 4 — Learning Agent
# Tracks: Skills · Courses · Reading · Notes · Knowledge Gaps
# ═══════════════════════════════════════════════════════════════════════════

async def learning_agent(ctx: dict) -> dict:
    entities  = ctx.get("extractedEntities", {})
    learning  = entities.get("learning") or {}
    habits    = entities.get("habits") or {}
    work      = entities.get("work") or {}
    weekly    = ctx.get("weeklyData", [])

    analysis     = []
    skill_logged = bool(learning.get("topic"))
    reading      = bool(habits.get("reading"))
    deep_work    = bool(habits.get("deepWork"))

    if skill_logged:
        topic = learning.get("topic", "")
        ltype = learning.get("type", "general")
        analysis.append(f"Learning logged: {topic} ({ltype}). Knowledge graph updated with new skill node.")

    if reading:
        analysis.append("Reading habit maintained. Long-form reading builds cognitive depth.")

    if deep_work:
        analysis.append("Deep work session logged — this is where real skill acquisition happens.")

    # Detect stagnation from weekly trend
    if len(weekly) >= 5:
        recent_focus = [d.get("focusHours", 0) for d in weekly[-5:]]
        avg_focus    = sum(recent_focus) / 5
        if avg_focus < 2 and not skill_logged and not reading:
            analysis.append("⚠️ Learning stagnation detected: low focus + no new skills this period.")
            ctx.setdefault("predictions", []).append({
                "type":        "learning_stagnation",
                "probability": 0.70,
                "timeframe":   "this week",
                "message":     "Learning activity has dropped significantly — skills may stagnate.",
            })

    if not analysis:
        analysis.append("No learning activity detected today. Even 15 min of reading compounds over time.")

    learning_score = ctx.get("twinBreakdown", {}).get("learning", 50)
    ctx["learningAnalysis"] = {
        "score":       learning_score,
        "topic":       learning.get("topic"),
        "analysis":    analysis,
        "skillLogged": skill_logged,
        "reading":     reading,
    }
    logger.info(f"[LearningAgent] score={learning_score} skill={skill_logged}")
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 5 — Habit Agent
# Finds: Broken Habits · Successful Streaks · Habit Loops · Patterns
# ═══════════════════════════════════════════════════════════════════════════

async def habit_agent(ctx: dict) -> dict:
    entities    = ctx.get("extractedEntities", {})
    habits      = entities.get("habits") or {}
    user_id     = ctx.get("userId")
    weekly      = ctx.get("weeklyData", [])

    completed   = [k for k, v in habits.items() if v]
    broken      = [k for k, v in habits.items() if not v and k]
    total       = len(habits)
    completion  = (len(completed) / total * 100) if total > 0 else 0

    analysis = []
    if completed:
        analysis.append(f"Habits completed: {', '.join(completed)}. Keep the streak alive!")
    if broken:
        analysis.append(f"Missed habits: {', '.join(broken)}. One miss is fine — two is a pattern.")

    # Streak detection from weekly data
    streak_risk = []
    if len(weekly) >= 3:
        recent_moods = [d.get("moodScore", 5) for d in weekly[-3:]]
        if all(m < 5 for m in recent_moods):
            streak_risk.append("mood_decline")
            analysis.append("3-day low mood streak detected — intervention may prevent further decline.")

    # Habit failure prediction
    if completion < 40 and total > 0:
        ctx.setdefault("predictions", []).append({
            "type":        "habit_failure",
            "probability": 0.72,
            "timeframe":   "this week",
            "message":     f"Habit completion at {completion:.0f}%. Streaks may break without re-engagement.",
        })
        analysis.append(f"⚠️ Habit completion rate is low ({completion:.0f}%). Breaking streaks hurts momentum.")

    # Positive habit loops
    has_meditation = bool(habits.get("meditation"))
    has_gym        = bool(habits.get("gym") or habits.get("exercise"))
    if has_meditation and has_gym:
        analysis.append("Meditation + exercise combination: your habit loop is supporting both mental and physical health.")

    ctx["habitAnalysis"] = {
        "score":       ctx.get("twinBreakdown", {}).get("productivity", 50),
        "completed":   completed,
        "broken":      broken,
        "completion":  round(completion),
        "analysis":    analysis,
        "streakRisk":  streak_risk,
    }
    logger.info(f"[HabitAgent] completion={completion:.0f}% completed={completed}")
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 6 — Reflection Agent
# Reads journals. Finds patterns. Detects emotional changes. Identifies
# causal chains like: Poor Sleep → Low Focus → Skipped Workout → Stress
# ═══════════════════════════════════════════════════════════════════════════

async def reflection_agent(ctx: dict) -> dict:
    burnout      = ctx.get("burnoutScore", 0)
    emotion      = ctx.get("emotion", {})
    patterns_in  = ctx.get("causalPatterns") or {}
    weekly       = ctx.get("weeklyData", [])
    entities     = ctx.get("extractedEntities", {})
    user_id      = ctx.get("userId")

    patterns  = []
    risk_chain = []

    # Causal chain detection (the core Neo4j insight)
    sleep_mood = patterns_in.get("sleepMood", {}) or {}
    ex_prod    = patterns_in.get("exerciseProductivity", {}) or {}

    if sleep_mood.get("avgMoodWithGoodSleep"):
        avg = sleep_mood["avgMoodWithGoodSleep"]
        patterns.append({
            "type":        "sleep_mood_correlation",
            "description": f"On days with 7+ hours sleep, your mood averages {avg:.1f}/10",
            "strength":    "strong",
            "chain":       "Good Sleep → Better Mood → Higher Productivity",
        })

    with_ex    = float(ex_prod.get("withExercise") or 0)
    without_ex = float(ex_prod.get("withoutExercise") or 0)
    if with_ex > without_ex and without_ex > 0:
        patterns.append({
            "type":        "exercise_productivity",
            "description": f"Exercise days give you {with_ex - without_ex:.1f} more focus hours",
            "strength":    "moderate",
            "chain":       "Exercise → Endorphins → Better Focus → More Output",
        })

    # Burnout causal chain construction
    if burnout >= 60:
        sleep_h = float((entities.get("sleep") or {}).get("hours") or 0)
        chain   = []
        if sleep_h < 6:     chain.append("Poor Sleep")
        if burnout >= 50:   chain.append("High Stress")
        habits = entities.get("habits") or {}
        if not any(habits.values()):
            chain.append("Skipped Habits")
        chain.append("Burnout Risk")
        risk_chain = chain
        patterns.append({
            "type":        "burnout_risk",
            "description": " → ".join(chain),
            "strength":    "critical",
            "chain":       " → ".join(chain),
        })

    # Detect emotional trajectory from weekly data
    if len(weekly) >= 5:
        moods  = [d.get("moodScore", 5) for d in weekly[-5:]]
        if moods[-1] < moods[0] - 2:
            patterns.append({
                "type":        "mood_decline_trajectory",
                "description": f"Mood dropped {moods[0]:.1f}→{moods[-1]:.1f} over 5 days. Intervention recommended.",
                "strength":    "critical",
                "chain":       "Mood Declining → At-Risk Trajectory",
            })

    # Missed goal prediction from goal insights
    goal_insights = ctx.get("goalInsights", {})
    blocked_goals = [k for k, v in goal_insights.items()
                     if k != "__warning__" and not v.get("linked")]
    if blocked_goals:
        ctx.setdefault("predictions", []).append({
            "type":        "missed_goal",
            "probability": 0.60,
            "timeframe":   "this month",
            "message":     f"Goals may be at risk: {', '.join(blocked_goals[:2])}",
        })

    # Stress spike prediction
    mood_score  = float((entities.get("mood") or {}).get("score") or 5)
    if mood_score < 4:
        ctx.setdefault("predictions", []).append({
            "type":        "stress_spike",
            "probability": 0.65,
            "timeframe":   "next 2 days",
            "message":     "Low mood may cascade into stress spike — consider proactive recovery.",
        })

    ctx["patterns"] = patterns
    ctx["riskChain"] = risk_chain
    logger.info(f"[ReflectionAgent] patterns={len(patterns)} burnout={burnout}")
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# AGENT 7 — Recommendation Agent
# Combines everything. Produces Today · This Week · Long-term plans.
# Generates Twin Response via Sarvam LLM in the user's language.
# ═══════════════════════════════════════════════════════════════════════════

async def recommendation_agent(ctx: dict) -> dict:
    entities     = ctx.get("extractedEntities", {})
    burnout      = ctx.get("burnoutScore", 0)
    patterns     = ctx.get("patterns", [])
    predictions  = ctx.get("predictions", [])
    health       = ctx.get("healthAnalysis", {})
    productivity = ctx.get("productivityAnalysis", {})
    learning     = ctx.get("learningAnalysis", {})
    habits       = ctx.get("habitAnalysis", {})

    graph_context = {
        "twinScore":        ctx.get("twinScore"),
        "burnoutScore":     burnout,
        "patterns":         patterns,
        "predictions":      predictions,
        "breakdown":        ctx.get("twinBreakdown"),
        "healthAlerts":     health.get("alerts", []),
        "productivityScore": productivity.get("score"),
        "learningScore":    learning.get("score"),
        "habitCompletion":  habits.get("completion"),
        "riskChain":        ctx.get("riskChain", []),
    }

    try:
        twin_response = await generate_twin_response(
            ctx.get("rawInput", ""),
            graph_context,
            ctx.get("responseLanguage", "en-IN"),
        )
    except Exception:
        twin_response = "I've updated your knowledge graph. Your patterns are being tracked — insights sharpen over time."

    ctx["twinResponse"] = twin_response

    # Structured recommendations: Today · This Week · Long Term
    today_recs, week_recs, long_recs = [], [], []

    # Today — based on current state
    if burnout >= 70:
        today_recs.append("🚨 Take a 20-minute walk outside immediately.")
        today_recs.append("Cancel non-essential meetings for today.")

    sleep_hours = float((entities.get("sleep") or {}).get("hours") or 0)
    if sleep_hours < 7:
        today_recs.append(f"Prioritize 7-8 hours sleep tonight (you logged {sleep_hours:.1f}h).")

    if health.get("stressLevel") == "high":
        today_recs.append("Try 5-minute box breathing: 4s inhale, 4s hold, 4s exhale, 4s hold.")

    if not habits.get("completed"):
        today_recs.append("Complete at least one small habit to maintain momentum.")

    # This week — based on patterns
    week_recs.append("Log your check-in every evening for 7 days to unlock causal patterns.")

    if burnout >= 50:
        week_recs.append("Schedule one no-meeting afternoon block this week.")

    if productivity.get("meetings", 0) > 6:
        week_recs.append("Batch meetings to Tuesday/Thursday — protect Monday/Wednesday for deep work.")

    if not learning.get("skillLogged"):
        week_recs.append("Dedicate 30 min to skill learning 3x this week to avoid learning stagnation.")

    for p in patterns:
        if p.get("type") == "sleep_mood_correlation":
            week_recs.append("Protect your 7-8h sleep window — it's your highest-ROI habit.")

    # Long term — growth trajectory
    long_recs.append("Build a consistent sleep schedule: same bedtime ± 30 min improves all dimensions.")
    long_recs.append("Review your Goal Graph weekly — unblock stuck goals by addressing their dependencies.")
    if learning.get("score", 0) < 60:
        long_recs.append("Start a 30-day learning sprint: one skill, 20 min/day, tracked in your knowledge graph.")

    ctx["recommendations"] = {
        "today":     today_recs,
        "thisWeek":  week_recs,
        "longTerm":  long_recs,
    }
    logger.info(f"[RecommendationAgent] response={bool(twin_response)} recs={len(today_recs)+len(week_recs)}")
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Goal Planner (inline helper — runs within Memory Agent context)
# ═══════════════════════════════════════════════════════════════════════════

async def _goal_planner(ctx: dict) -> dict:
    user_id  = ctx.get("userId")
    entities = ctx.get("extractedEntities", {})
    goal_insights: dict[str, Any] = {}

    for g in (entities.get("goals") or []):
        title    = g.get("title", "")
        progress = g.get("progress", 0)
        if title and user_id:
            try:
                await upsert_goal(user_id, title)
                goal_insights[title] = {"progress": progress, "linked": True}
            except Exception:
                goal_insights[title] = {"progress": progress, "linked": False}

    if ctx.get("burnoutScore", 0) >= 60:
        goal_insights["__warning__"] = "High burnout risk may block goal progress."

    ctx["goalInsights"] = goal_insights
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Prediction Engine (burnout + pipeline predictions)
# ═══════════════════════════════════════════════════════════════════════════

async def _prediction_engine(ctx: dict) -> dict:
    predictions = ctx.get("predictions", [])  # may already have some from agents
    burnout     = ctx.get("burnoutScore", 0)
    entities    = ctx.get("extractedEntities", {})

    # Burnout prediction
    if burnout >= 70:
        predictions.append({
            "type": "burnout", "probability": 0.87,
            "timeframe": "3 days",
            "message":   "Burnout is imminent without immediate rest and recovery."
        })
    elif burnout >= 50:
        predictions.append({
            "type": "burnout", "probability": 0.55,
            "timeframe": "7 days",
            "message":   "Moderate burnout risk building this week."
        })

    # Sleep → productivity
    sleep_hours = float((entities.get("sleep") or {}).get("hours") or 0)
    if sleep_hours < 6:
        predictions.append({
            "type": "productivity_decline", "probability": 0.73,
            "timeframe": "tomorrow",
            "message":   f"Sleep debt ({sleep_hours:.1f}h) will cut focus capacity by ~30%."
        })

    # Mood prediction
    mood_score = float((entities.get("mood") or {}).get("score") or 5)
    if mood_score < 4:
        predictions.append({
            "type": "mood_decline", "probability": 0.65,
            "timeframe": "2 days",
            "message":   "Low mood may persist — consider a social interaction or change of environment."
        })

    ctx["predictions"] = predictions
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline Definition
# Pre-processors + 7 Specialized Agents
# ═══════════════════════════════════════════════════════════════════════════

PIPELINE_STAGES = [
    # Input pre-processing
    ("Language Detection",  _preprocess_language),
    ("Emotion Detection",   _preprocess_emotion),
    ("Entity Extraction",   _preprocess_entity),
    # 7 Specialized Agents
    ("Memory Agent",        memory_agent),
    ("Health Agent",        health_agent),
    ("Productivity Agent",  productivity_agent),
    ("Learning Agent",      learning_agent),
    ("Habit Agent",         habit_agent),
    ("Reflection Agent",    reflection_agent),
    # Goal planner + predictions (run before final recommendation)
    ("Goal Planner",        _goal_planner),
    ("Prediction Engine",   _prediction_engine),
    # Final agent
    ("Recommendation Agent", recommendation_agent),
]

SPECIALIZED_AGENTS = [
    "Memory Agent", "Health Agent", "Productivity Agent",
    "Learning Agent", "Habit Agent", "Reflection Agent", "Recommendation Agent",
]


async def run_agent_pipeline(
    raw_input: str,
    user_id:   str | None = None,
    preferred_language: str = "en-IN",
    weekly_data: list = None,
) -> dict:
    ctx: dict[str, Any] = {
        "rawInput":          raw_input,
        "userId":            user_id,
        "preferredLanguage": preferred_language,
        "weeklyData":        weekly_data or [],
        "startTime":         time.time(),
        "stages":            [],
        "predictions":       [],  # agents can append predictions
    }

    for name, fn in PIPELINE_STAGES:
        t = time.time()
        try:
            ctx = await fn(ctx)
            ctx["stages"].append({
                "agent":     name,
                "status":    "done",
                "ms":        int((time.time() - t) * 1000),
                "isSpecialized": name in SPECIALIZED_AGENTS,
            })
        except Exception as exc:
            logger.error(f"[Pipeline] {name} failed: {exc}", exc_info=True)
            ctx["stages"].append({
                "agent":     name,
                "status":    "error",
                "ms":        int((time.time() - t) * 1000),
                "error":     str(exc),
                "isSpecialized": name in SPECIALIZED_AGENTS,
            })

    return {
        "success":              True,
        "twinResponse":         ctx.get("twinResponse", "Entry logged to your knowledge graph."),
        "twinScore":            ctx.get("twinScore", 50),
        "twinBreakdown":        ctx.get("twinBreakdown", {}),
        "burnoutScore":         ctx.get("burnoutScore", 0),
        "burnoutTrend":         "stable",
        "twinSummary":          ctx.get("twinSummary", ""),
        "emotion":              ctx.get("emotion", {}),
        "detectedLanguage":     ctx.get("detectedLanguage", "en-IN"),
        "detectedLanguageName": ctx.get("detectedLanguageName", "English"),
        "patterns":             ctx.get("patterns", []),
        "predictions":          ctx.get("predictions", []),
        "recommendations":      ctx.get("recommendations", {"today": [], "thisWeek": [], "longTerm": []}),
        "goalInsights":         ctx.get("goalInsights", {}),
        "causalPatterns":       ctx.get("causalPatterns", {}),
        "healthAnalysis":       ctx.get("healthAnalysis", {}),
        "productivityAnalysis": ctx.get("productivityAnalysis", {}),
        "learningAnalysis":     ctx.get("learningAnalysis", {}),
        "habitAnalysis":        ctx.get("habitAnalysis", {}),
        "riskChain":            ctx.get("riskChain", []),
        "memoryUpdated":        ctx.get("memoryUpdated", False),
        "responseLanguage":     ctx.get("responseLanguage", preferred_language),
        "stages":               ctx["stages"],
        "processingMs":         int((time.time() - ctx["startTime"]) * 1000),
    }
