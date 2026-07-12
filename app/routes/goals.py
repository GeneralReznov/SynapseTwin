"""Goal management routes — graph-linked goals with AI breakdown."""
import json
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import require_auth
from app.services.neo4j_service import upsert_goal, link_goal_to_habit
from app.services.sarvam import chat_completion
from app.db.neo4j_db import run_query

router = APIRouter(prefix="/api/goals", tags=["goals"])


class GoalBody(BaseModel):
    title: str
    category: Optional[str] = "general"
    targetDate: Optional[str] = None
    habits: Optional[List[str]] = []
    skills: Optional[List[str]] = []


class ProgressBody(BaseModel):
    title: str
    progress: float


@router.post("/")
async def create_goal(body: GoalBody, current_user: dict = Depends(require_auth)):
    if not body.title:
        raise HTTPException(status_code=400, detail="title is required")

    user_id = current_user["userId"]
    await upsert_goal(user_id, body.title, body.category or "general", body.targetDate)

    for habit in (body.habits or []):
        try:
            await link_goal_to_habit(user_id, body.title, habit, "DEPENDS_ON")
        except Exception:
            pass

    for skill in (body.skills or []):
        try:
            await run_query(
                """MATCH (g:Goal {title:$goal, userId:$userId}), (sk:Skill {name:$skill, userId:$userId})
                   MERGE (g)-[:LINKED_TO]->(sk)""",
                {"userId": user_id, "goal": body.title, "skill": skill},
            )
        except Exception:
            pass

    ai_breakdown = None
    try:
        r = await chat_completion(
            "You are a goal planning assistant. Return a JSON object with: "
            "milestones (array of 3 strings), weeklyActions (array of 3 strings), "
            "blockers (array of potential blockers), successMetric (string). Return ONLY JSON.",
            f"Goal: {body.title} | Category: {body.category} | Target: {body.targetDate or 'ongoing'}",
            json_mode=True,
        )
        if r["success"]:
            ai_breakdown = json.loads(r["content"])
    except Exception:
        pass

    return {"success": True, "goal": body.title, "habits": body.habits, "skills": body.skills, "aiBreakdown": ai_breakdown}


@router.get("/")
async def list_goals(current_user: dict = Depends(require_auth)):
    rows = await run_query(
        """MATCH (u:User {id:$userId})-[:HAS_GOAL]->(g:Goal)
           OPTIONAL MATCH (g)-[:DEPENDS_ON]->(h:Habit)
           OPTIONAL MATCH (g)-[:LINKED_TO]->(sk:Skill)
           OPTIONAL MATCH (g)-[:BLOCKED_BY]->(blocker)
           RETURN g.title AS title, g.category AS category, g.progress AS progress,
                  g.targetDate AS targetDate, g.createdAt AS createdAt,
                  collect(DISTINCT h.name) AS dependsOnHabits,
                  collect(DISTINCT sk.name) AS linkedSkills,
                  collect(DISTINCT labels(blocker)[0]) AS blockedBy""",
        {"userId": current_user["userId"]},
    )
    goals = [
        {
            "title":           r["title"],
            "category":        r.get("category", "general"),
            "progress":        r.get("progress", 0),
            "targetDate":      r.get("targetDate"),
            "createdAt":       r.get("createdAt"),
            "dependsOnHabits": [h for h in (r.get("dependsOnHabits") or []) if h],
            "linkedSkills":    [s for s in (r.get("linkedSkills") or []) if s],
            "blockedBy":       [b for b in (r.get("blockedBy") or []) if b],
            "isBlocked":       any(b for b in (r.get("blockedBy") or []) if b),
        }
        for r in rows
    ]
    return {"goals": goals}


@router.patch("/progress")
async def update_progress(body: ProgressBody, current_user: dict = Depends(require_auth)):
    if not body.title or body.progress is None:
        raise HTTPException(status_code=400, detail="title and progress required")
    from datetime import datetime, timezone
    await run_query(
        """MATCH (u:User {id:$userId})-[:HAS_GOAL]->(g:Goal {title:$title})
           SET g.progress=$progress, g.updatedAt=$now""",
        {"userId": current_user["userId"], "title": body.title,
         "progress": body.progress, "now": datetime.now(timezone.utc).isoformat()},
    )
    return {"success": True}


@router.get("/causal-chain")
async def causal_chain(title: str, current_user: dict = Depends(require_auth)):
    rows = await run_query(
        """MATCH path=(g:Goal {title:$title, userId:$userId})-[*1..3]->(n)
           RETURN [node IN nodes(path) | {id: coalesce(node.id, node.name, node.title),
                   type: labels(node)[0], label: coalesce(node.name, node.title, labels(node)[0])}] AS chain,
                  [rel IN relationships(path) | type(rel)] AS rels""",
        {"userId": current_user["userId"], "title": title},
    )
    return {"chains": rows}
