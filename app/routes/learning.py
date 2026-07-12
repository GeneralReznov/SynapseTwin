"""Learning routes — Coursera & Udemy integrations with role-based AI recommendations."""
from __future__ import annotations
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import require_auth
from app.services.groq_service import generate_course_recommendations
from app.services.sarvam import chat_completion as sarvam_chat
from app.db.neo4j_db import run_query
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["learning"])


# ── Models ─────────────────────────────────────────────────────────────────────

class CourseProgressBody(BaseModel):
    platform:     str             # "coursera" | "udemy"
    course_title: str
    course_url:   str
    progress:     float           # 0.0 – 1.0
    skills:       Optional[list[str]] = []


class LearningGoalBody(BaseModel):
    topic:    str
    platform: Optional[str] = "both"
    level:    Optional[str] = "intermediate"


# ── AI helper ─────────────────────────────────────────────────────────────────

async def _ai(system: str, user: str, json_mode: bool = False) -> dict:
    """Sarvam AI is primary; automatically falls back to Groq internally if Sarvam is unavailable."""
    return await sarvam_chat(system, user, json_mode=json_mode)


# ── Profile helpers ───────────────────────────────────────────────────────────

async def _get_user_profile(user_id: str) -> dict:
    """Get extended user profile for course recommendations."""
    try:
        rows = await run_query(
            """MATCH (u:User {id:$userId})
               RETURN u.name AS name, u.role AS role, u.email AS email,
                      u.jobTitle AS jobTitle, u.department AS department,
                      u.teamId AS teamId
               LIMIT 1""",
            {"userId": user_id},
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


async def _get_recent_learning_topics(user_id: str) -> list[str]:
    """Get topics from recent learning sessions in Neo4j."""
    try:
        rows = await run_query(
            """MATCH (u:User {id:$userId})-[:LOGGED]->(d:DailyLog)
               WHERE d.learningTopic IS NOT NULL
               RETURN d.learningTopic AS topic
               ORDER BY d.date DESC LIMIT 10""",
            {"userId": user_id},
        )
        return [r["topic"] for r in rows if r.get("topic")]
    except Exception:
        return []


def _infer_role_context(profile: dict) -> tuple[str, str, str]:
    """Infer job_title, department, role from stored profile data."""
    name       = profile.get("name", "")
    role       = profile.get("role", "user")
    job_title  = profile.get("jobTitle", "")
    department = profile.get("department", "")
    email      = profile.get("email", "")

    # If job title is missing, try to infer from role or email domain
    if not job_title:
        role_lower = role.lower()
        if "manager" in role_lower:     job_title = "Engineering Manager"
        elif "engineer" in role_lower:  job_title = "Software Engineer"
        elif "designer" in role_lower:  job_title = "Product Designer"
        elif "data" in role_lower:      job_title = "Data Scientist"
        elif "product" in role_lower:   job_title = "Product Manager"
        else:                           job_title = "Professional"

    if not department:
        jt_lower = job_title.lower()
        if any(k in jt_lower for k in ["engineer", "developer", "tech"]): department = "Engineering"
        elif any(k in jt_lower for k in ["manager", "lead"]):             department = "Engineering Management"
        elif "design" in jt_lower:                                         department = "Design"
        elif "data" in jt_lower:                                           department = "Data & Analytics"
        elif "product" in jt_lower:                                        department = "Product"
        else:                                                              department = "General"

    return job_title, department, role


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/recommendations")
async def get_recommendations(
    platform: str = "both",
    current_user: dict = Depends(require_auth),
):
    """Get role-based course recommendations from Coursera and Udemy."""
    user_id = current_user["userId"]

    # Get user profile
    profile = await _get_user_profile(user_id)
    recent_topics = await _get_recent_learning_topics(user_id)

    job_title, department, role = _infer_role_context({
        **profile,
        "role": current_user.get("role", profile.get("role", "user")),
    })

    name = profile.get("name", current_user.get("name", "User"))

    # Generate recommendations
    recommendations = await generate_course_recommendations(
        user_name    = name,
        user_role    = role,
        job_title    = job_title,
        department   = department,
        recent_topics= recent_topics,
        platform     = platform,
    )

    # Filter by requested platform
    if platform == "coursera":
        recommendations.pop("udemy", None)
    elif platform == "udemy":
        recommendations.pop("coursera", None)

    return {
        "success":    True,
        "profile":    {"name": name, "jobTitle": job_title, "department": department},
        "platform":   platform,
        **recommendations,
    }


@router.post("/progress")
async def log_course_progress(
    body: CourseProgressBody,
    current_user: dict = Depends(require_auth),
):
    """Log course progress to Neo4j knowledge graph."""
    user_id = current_user["userId"]
    now     = datetime.now(timezone.utc).isoformat()

    try:
        await run_query(
            """MERGE (u:User {id:$userId})
               MERGE (c:Course {url:$url})
               ON CREATE SET c.title=$title, c.platform=$platform, c.createdAt=$now
               MERGE (u)-[r:LEARNING]->(c)
               ON CREATE SET r.startedAt=$now
               SET r.progress=$progress, r.updatedAt=$now, c.skills=$skills""",
            {
                "userId":   user_id,
                "url":      body.course_url,
                "title":    body.course_title,
                "platform": body.platform,
                "progress": body.progress,
                "skills":   body.skills or [],
                "now":      now,
            },
        )
        logged = True
    except Exception as e:
        logger.warning(f"Course progress Neo4j error: {e}")
        logged = False

    # Generate AI encouragement
    pct = round(body.progress * 100)
    system = "You are SynapseTwin, a supportive learning coach. Be brief (1 sentence), warm, and encouraging."
    user_msg = f"User completed {pct}% of '{body.course_title}' on {body.platform.title()}. Give them a brief motivational message."

    result = await _ai(system, user_msg)
    message = result.get("content", f"Great progress! {pct}% through '{body.course_title}' — keep the momentum going!") \
              if result.get("success") else f"Awesome! {pct}% complete — you're building real expertise!"

    return {
        "success":    True,
        "logged":     logged,
        "progress":   pct,
        "message":    message,
        "skillsAdded": body.skills or [],
    }


@router.get("/history")
async def get_learning_history(
    limit: int = 10,
    current_user: dict = Depends(require_auth),
):
    """Get user's course history from Neo4j."""
    try:
        rows = await run_query(
            """MATCH (u:User {id:$userId})-[r:LEARNING]->(c:Course)
               RETURN c.title AS title, c.platform AS platform, c.url AS url,
                      r.progress AS progress, r.updatedAt AS updatedAt, c.skills AS skills
               ORDER BY r.updatedAt DESC LIMIT $limit""",
            {"userId": current_user["userId"], "limit": limit},
        )
        return {"success": True, "courses": rows}
    except Exception as e:
        logger.error(f"Learning history error: {e}")
        return {"success": True, "courses": []}


@router.post("/ai-advice")
async def get_learning_advice(
    body: LearningGoalBody,
    current_user: dict = Depends(require_auth),
):
    """Get AI-powered learning path advice for a specific topic."""
    user_id = current_user["userId"]
    profile = await _get_user_profile(user_id)
    job_title, department, role = _infer_role_context({
        **profile,
        "role": current_user.get("role", profile.get("role", "user")),
    })

    system = (
        "You are SynapseTwin's Learning Intelligence Engine. You create precise, actionable learning paths "
        "for professionals based on their role and goals. Be specific about time commitments and outcomes."
    )
    user_msg = (
        f"Professional: {job_title} in {department}\n"
        f"Learning Goal: Master '{body.topic}'\n"
        f"Preferred Platform: {body.platform}\n"
        f"Target Level: {body.level}\n\n"
        "Provide a specific 3-step learning path (what to learn first, second, third), "
        "estimated time per step, and the key skill outcome. Keep it under 150 words. Be practical."
    )

    result = await _ai(system, user_msg)
    advice = result.get("content", f"Start with fundamentals of {body.topic}, then apply through projects, and finally tackle advanced concepts through specialized courses.") \
             if result.get("success") else f"Build your {body.topic} expertise step by step — start with foundations, then hands-on practice, then advanced specialization."

    return {
        "success": True,
        "topic":   body.topic,
        "advice":  advice,
        "profile": {"jobTitle": job_title, "department": department},
    }


@router.get("/stats")
async def get_learning_stats(current_user: dict = Depends(require_auth)):
    """Get aggregated learning statistics for the user."""
    user_id = current_user["userId"]
    try:
        rows = await run_query(
            """MATCH (u:User {id:$userId})-[r:LEARNING]->(c:Course)
               RETURN count(c) AS totalCourses,
                      avg(r.progress) AS avgProgress,
                      collect(DISTINCT c.platform) AS platforms,
                      collect(c.skills) AS allSkills""",
            {"userId": user_id},
        )
        stats = rows[0] if rows else {}

        # Flatten skills
        all_skills = []
        for skill_list in (stats.get("allSkills") or []):
            if isinstance(skill_list, list):
                all_skills.extend(skill_list)
        unique_skills = list(set(s for s in all_skills if s))

        completed = await run_query(
            """MATCH (u:User {id:$userId})-[r:LEARNING]->(c:Course)
               WHERE r.progress >= 1.0 RETURN count(c) AS completed""",
            {"userId": user_id},
        )

        return {
            "success":       True,
            "totalCourses":  stats.get("totalCourses", 0),
            "completedCourses": (completed[0].get("completed", 0) if completed else 0),
            "avgProgress":   round((stats.get("avgProgress") or 0) * 100, 1),
            "platforms":     stats.get("platforms") or [],
            "skillsLearned": unique_skills[:10],
        }
    except Exception as e:
        logger.error(f"Learning stats error: {e}")
        return {"success": True, "totalCourses": 0, "completedCourses": 0, "avgProgress": 0, "platforms": [], "skillsLearned": []}
