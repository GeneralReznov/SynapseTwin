"""Twin Score Engine — computes a 0-100 wellness alignment score."""
from __future__ import annotations


def _clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


def compute_twin_score(data: dict) -> dict:
    sleep             = data.get("sleep") or {}
    mood              = data.get("mood") or {}
    exercise          = data.get("exercise") or {}
    habits            = data.get("habits") or {}
    work              = data.get("work") or {}
    learning          = data.get("learning") or {}
    social_interaction = data.get("socialInteraction") or None
    water_intake      = float(data.get("waterIntake") or 0)
    weekly_data       = data.get("weeklyData") or []

    # ── Physical ──────────────────────────────────────────────────────────────
    physical = 50.0
    sleep_hours = float(sleep.get("hours") or 0)
    if sleep_hours >= 8:   physical += 25
    elif sleep_hours >= 7: physical += 18
    elif sleep_hours >= 6: physical += 8
    elif sleep_hours > 0:  physical -= 10

    if exercise.get("done"):
        dur = int(exercise.get("durationMinutes") or 0)
        if dur >= 60:   physical += 20
        elif dur >= 30: physical += 12
        else:           physical += 5

    if water_intake >= 2.5:
        physical += 5
    physical = _clamp(physical)

    # ── Mental ────────────────────────────────────────────────────────────────
    mental = 50.0
    mood_score = float(mood.get("score") or 5)
    mental += (mood_score - 5) * 5

    energy = str(mood.get("energyLevel") or "Medium").lower()
    if energy == "high": mental += 10
    elif energy == "low": mental -= 10

    stress = str(mood.get("stressLevel") or "Medium").lower()
    if stress == "low":  mental += 10
    elif stress == "high": mental -= 15
    mental = _clamp(mental)

    # ── Productivity ──────────────────────────────────────────────────────────
    productivity = 40.0
    focus_hours = float(work.get("focusHours") or 0)
    productivity += min(focus_hours / 8, 1) * 40

    meetings = int(work.get("meetings") or 0)
    if meetings > 6:          productivity -= 10
    elif 2 <= meetings <= 4:  productivity += 5

    habit_keys = list(habits.keys())
    completed  = sum(1 for v in habits.values() if v)
    habit_bonus = (completed / len(habit_keys) * 15) if habit_keys else 0
    productivity += habit_bonus
    productivity = _clamp(productivity)

    # ── Learning ──────────────────────────────────────────────────────────────
    learning_score = 40.0
    if learning and learning.get("topic"):  learning_score += 25
    if habits.get("reading"):               learning_score += 15
    if habits.get("deepWork"):              learning_score += 10
    if len(weekly_data) >= 3:
        recent = weekly_data[-3:]
        avg_focus = sum(d.get("focusHours", 0) for d in recent) / 3
        if avg_focus >= 4:
            learning_score += 10
    learning_score = _clamp(learning_score)

    # ── Social ────────────────────────────────────────────────────────────────
    social = 50.0
    if social_interaction and social_interaction.get("person"): social += 20
    if habits.get("meditation"):                                social += 10
    social = _clamp(social)

    # ── Weighted composite ────────────────────────────────────────────────────
    twin_score = round(
        physical * 0.25 +
        mental   * 0.25 +
        productivity * 0.25 +
        learning_score * 0.15 +
        social   * 0.10
    )

    # ── Burnout risk ──────────────────────────────────────────────────────────
    burnout = 0.0
    if mood_score < 5:  burnout += 30
    elif mood_score < 7: burnout += 15
    if sleep_hours < 6:  burnout += 25
    elif sleep_hours < 7: burnout += 10
    if stress == "high":   burnout += 25
    elif stress == "medium": burnout += 10
    if energy == "low":    burnout += 20
    skip_rate = (len(habit_keys) - completed) / len(habit_keys) if habit_keys else 0
    burnout += skip_rate * 15
    if len(weekly_data) >= 5:
        recent5 = [d.get("moodScore", 5) for d in weekly_data[-5:]]
        if recent5[-1] < recent5[0] - 1.5:
            burnout += 15
    burnout = _clamp(burnout)

    # ── Text summary ──────────────────────────────────────────────────────────
    if twin_score >= 80:
        summary = "You're thriving today — excellent alignment across all dimensions."
    elif twin_score >= 65:
        summary = "Good momentum. A few tweaks could push you further."
    elif twin_score >= 50:
        summary = "Moderate alignment. Focus on sleep and habits to improve."
    else:
        summary = "Your twin score needs attention. Prioritize rest and self-care."

    if burnout >= 70:
        summary += " ⚠️ High burnout risk detected — please slow down."
    elif burnout >= 40:
        summary += " Watch your stress levels this week."

    return {
        "twinScore":  twin_score,
        "burnoutScore": int(burnout),
        "summary":    summary,
        "breakdown": {
            "physical":     round(physical),
            "mental":       round(mental),
            "productivity": round(productivity),
            "learning":     round(learning_score),
            "social":       round(social),
        },
    }
