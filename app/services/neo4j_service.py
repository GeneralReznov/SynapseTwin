"""Neo4j knowledge graph CRUD for SynapseTwin."""
from __future__ import annotations
import logging
from datetime import date, timezone, datetime
from app.db.neo4j_db import run_query

logger = logging.getLogger(__name__)


# ── User Management ────────────────────────────────────────────────────────────

async def create_user(
    user_id: str, name: str, email: str,
    role: str = "user", password_hash: str = None, team_id: str = None,
) -> dict | None:
    created_at = datetime.now(timezone.utc).isoformat()
    rows = await run_query(
        """MERGE (u:User {email: $email})
           ON CREATE SET u.id=$userId, u.name=$name, u.createdAt=$createdAt,
                         u.twinScore=50, u.language='en-IN', u.role=$role,
                         u.passwordHash=$passwordHash, u.teamId=$teamId
           ON MATCH SET u.name=$name
           RETURN u.id AS id, u.name AS name, u.email AS email,
                  u.twinScore AS twinScore, u.role AS role,
                  u.passwordHash AS passwordHash, u.teamId AS teamId""",
        {"userId": user_id, "name": name, "email": email,
         "createdAt": created_at, "role": role, "passwordHash": password_hash,
         "teamId": team_id},
    )
    return rows[0] if rows else None


async def assign_user_team(user_id: str, team_id: str, role: str = None) -> bool:
    """Assign a user to a team (and optionally upgrade their role). Returns True if user was found."""
    set_clause = "u.teamId=$teamId"
    params: dict = {"userId": user_id, "teamId": team_id}
    if role:
        set_clause += ", u.role=$role"
        params["role"] = role
    rows = await run_query(
        f"MATCH (u:User {{id:$userId}}) SET {set_clause} RETURN u.id AS id",
        params,
    )
    return bool(rows)


async def find_user_by_email(email: str) -> dict | None:
    rows = await run_query(
        """MATCH (u:User {email: $email})
           RETURN u.id AS id, u.name AS name, u.email AS email,
                  u.twinScore AS twinScore, u.language AS language,
                  u.role AS role, u.passwordHash AS passwordHash""",
        {"email": email.lower().strip()},
    )
    return rows[0] if rows else None


async def update_user_twin_score(user_id: str, twin_score: int):
    await run_query(
        "MATCH (u:User {id:$userId}) SET u.twinScore=$twinScore, u.updatedAt=$now",
        {"userId": user_id, "twinScore": twin_score, "now": datetime.now(timezone.utc).isoformat()},
    )


async def update_user_language(user_id: str, language: str):
    await run_query(
        "MATCH (u:User {id:$userId}) SET u.language=$language",
        {"userId": user_id, "language": language},
    )


# ── Daily Log ──────────────────────────────────────────────────────────────────

async def save_daily_log(user_id: str, data: dict):
    today       = data.get("date") or date.today().isoformat()
    mood        = data.get("mood") or {}
    sleep       = data.get("sleep") or {}
    exercise    = data.get("exercise") or {}
    habits      = data.get("habits") or {}
    work        = data.get("work") or {}
    learning    = data.get("learning") or {}
    social      = data.get("socialInteraction") or {}
    water       = float(data.get("waterIntake") or 0)
    burnout_sc  = float(data.get("burnoutScore") or 0)
    twin_sc     = float(data.get("twinScore") or 50)
    emotion     = data.get("emotion") or {}
    language    = data.get("language") or "en-IN"
    raw_input   = data.get("rawInput") or ""

    import time
    prefix    = hex(int(time.time() * 1000))[2:]
    mood_id   = f"mood_{prefix}"
    sleep_id  = f"sleep_{prefix}"
    ex_id     = f"ex_{prefix}"
    focus_id  = f"focus_{prefix}"
    burnout_id = f"burnout_{prefix}"

    # Clear today's nodes — using date() conversion so it matches stored Date type
    await run_query(
        """MERGE (u:User {id:$userId})
           WITH u
           OPTIONAL MATCH (u)-[:LOGGED]->(m:Mood {date:date($date)}) DETACH DELETE m
           WITH u
           OPTIONAL MATCH (u)-[:SLEPT]->(s:Sleep {date:date($date)}) DETACH DELETE s
           WITH u
           OPTIONAL MATCH (u)-[:EXERCISED]->(e:Exercise {date:date($date)}) DETACH DELETE e
           WITH u
           OPTIONAL MATCH (u)-[:FOCUSED]->(f:FocusSession {date:date($date)}) DETACH DELETE f
           WITH u
           OPTIONAL MATCH (u)-[:HAS_BURNOUT_RISK]->(b:BurnoutRisk {date:date($date)}) DETACH DELETE b""",
        {"userId": user_id, "date": today},
    )

    # Mood — store date as Neo4j Date type so date arithmetic in WHERE clauses works
    if mood.get("score") is not None:
        await run_query(
            """MATCH (u:User {id:$userId})
               CREATE (m:Mood {id:$moodId, date:date($date), score:$score,
                               energyLevel:$energy, stressLevel:$stress,
                               notes:$notes, emotion:$emotion, language:$language})
               CREATE (u)-[:LOGGED]->(m)""",
            {"userId": user_id, "moodId": mood_id, "date": today,
             "score": mood.get("score", 5), "energy": mood.get("energyLevel", "Medium"),
             "stress": mood.get("stressLevel", "Medium"),
             "notes": mood.get("notes") or raw_input[:500],
             "emotion": emotion.get("emotion", "neutral"), "language": language},
        )

    # Sleep
    if sleep.get("hours"):
        await run_query(
            """MATCH (u:User {id:$userId})
               CREATE (s:Sleep {id:$sleepId, date:date($date), hours:$hours, quality:$quality})
               CREATE (u)-[:SLEPT]->(s)""",
            {"userId": user_id, "sleepId": sleep_id, "date": today,
             "hours": sleep.get("hours", 0), "quality": sleep.get("quality", "Fair")},
        )

    # Exercise
    if exercise.get("done"):
        await run_query(
            """MATCH (u:User {id:$userId})
               CREATE (e:Exercise {id:$exId, date:date($date), type:$type,
                                   durationMinutes:$dur, intensity:$intensity})
               CREATE (u)-[:EXERCISED]->(e)""",
            {"userId": user_id, "exId": ex_id, "date": today,
             "type": exercise.get("type", "General"), "dur": exercise.get("durationMinutes", 0),
             "intensity": exercise.get("intensity", "Medium")},
        )

    # Habits
    for habit_name, done in habits.items():
        if done:
            await run_query(
                """MATCH (u:User {id:$userId})
                   MERGE (h:Habit {name:$name, userId:$userId})
                   SET h.lastDone=$date, h.streak=coalesce(h.streak,0)+1
                   MERGE (u)-[:TRACKED {date:$date}]->(h)""",
                {"userId": user_id, "name": habit_name, "date": today},
            )

    # Focus / work
    if work.get("focusHours"):
        await run_query(
            """MATCH (u:User {id:$userId})
               CREATE (f:FocusSession {id:$focusId, date:date($date),
                                       hours:$hours, meetings:$meetings, stressful:$stressful})
               CREATE (u)-[:FOCUSED]->(f)""",
            {"userId": user_id, "focusId": focus_id, "date": today,
             "hours": work.get("focusHours", 0),
             "meetings": work.get("meetings", 0),
             "stressful": work.get("stressful", False)},
        )

    # Burnout risk
    if burnout_sc > 0:
        await run_query(
            """MATCH (u:User {id:$userId})
               CREATE (b:BurnoutRisk {id:$bid, date:date($date), score:$score})
               CREATE (u)-[:HAS_BURNOUT_RISK]->(b)""",
            {"userId": user_id, "bid": burnout_id, "date": today, "score": burnout_sc},
        )

    # Update twin score on User node
    await run_query(
        "MATCH (u:User {id:$userId}) SET u.twinScore=$score, u.updatedAt=$now",
        {"userId": user_id, "score": twin_sc, "now": datetime.now(timezone.utc).isoformat()},
    )

    # Learning
    if learning and learning.get("topic"):
        await run_query(
            """MATCH (u:User {id:$userId})
               MERGE (sk:Skill {name:$topic, userId:$userId})
               SET sk.lastLearned=$date, sk.type=$type
               MERGE (u)-[:LEARNED {date:$date}]->(sk)""",
            {"userId": user_id, "topic": learning["topic"], "date": today,
             "type": learning.get("type", "general")},
        )

    # Social
    if social and social.get("person"):
        await run_query(
            """MATCH (u:User {id:$userId})
               MERGE (p:Person {name:$person, userId:$userId})
               SET p.relationship=$rel
               MERGE (u)-[:INTERACTED_WITH {date:$date}]->(p)""",
            {"userId": user_id, "person": social["person"],
             "rel": social.get("relationship", "friend"), "date": today},
        )


# ── Goal Management ────────────────────────────────────────────────────────────

async def upsert_goal(user_id: str, title: str, category: str = "general", target_date: str = None):
    await run_query(
        """MATCH (u:User {id:$userId})
           MERGE (g:Goal {title:$title, userId:$userId})
           ON CREATE SET g.category=$category, g.progress=0,
                         g.targetDate=$targetDate, g.createdAt=$now
           ON MATCH  SET g.category=$category, g.targetDate=$targetDate
           MERGE (u)-[:HAS_GOAL]->(g)""",
        {"userId": user_id, "title": title, "category": category,
         "targetDate": target_date, "now": datetime.now(timezone.utc).isoformat()},
    )


_ALLOWED_REL_TYPES = frozenset({"CAUSES", "BLOCKED_BY", "DEPENDS_ON", "AFFECTS", "INFLUENCES"})


async def link_goal_to_habit(user_id: str, goal_title: str, habit_name: str, rel_type: str = "DEPENDS_ON"):
    safe_rel = rel_type.upper() if rel_type.upper() in _ALLOWED_REL_TYPES else "DEPENDS_ON"
    await run_query(
        f"""MATCH (g:Goal {{title:$goal, userId:$userId}}),
                  (h:Habit {{name:$habit, userId:$userId}})
            MERGE (g)-[:{safe_rel}]->(h)""",
        {"userId": user_id, "goal": goal_title, "habit": habit_name},
    )


async def save_causal_link(
    user_id: str,
    source_label: str,
    target_label: str,
    rel_type: str = "CAUSES",
    props: dict = None,
):
    """
    Create a causal relationship between two concept nodes for a user.
    rel_type can be: CAUSES, BLOCKED_BY, DEPENDS_ON, AFFECTS, INFLUENCES
    """
    props = props or {}
    now = datetime.now(timezone.utc).isoformat()
    safe_rel = rel_type.upper() if rel_type.upper() in _ALLOWED_REL_TYPES else "CAUSES"
    await run_query(
        f"""MATCH (u:User {{id:$userId}})
            MERGE (s:Concept {{name:$source, userId:$userId}})
            MERGE (t:Concept {{name:$target, userId:$userId}})
            MERGE (s)-[r:{safe_rel}]->(t)
            SET r.createdAt=$now, r.reason=$reason
            MERGE (u)-[:HAS_CONCEPT]->(s)
            MERGE (u)-[:HAS_CONCEPT]->(t)""",
        {
            "userId": user_id, "source": source_label, "target": target_label,
            "now": now, "reason": props.get("reason", ""),
        },
    )


async def log_environment(user_id: str, env_data: dict):
    """Store Environment Graph nodes: Weather, Location, Noise, Commute."""
    today = date.today().isoformat()
    await run_query(
        """MATCH (u:User {id:$userId})
           MERGE (e:Environment {date:$date, userId:$userId})
           SET e.weather=$weather, e.location=$location,
               e.noise=$noise, e.commute=$commute,
               e.updatedAt=$now
           MERGE (u)-[:EXPERIENCED]->(e)""",
        {
            "userId":   user_id,
            "date":     today,
            "weather":  env_data.get("weather", ""),
            "location": env_data.get("location", ""),
            "noise":    env_data.get("noise", ""),
            "commute":  env_data.get("commute", ""),
            "now":      datetime.now(timezone.utc).isoformat(),
        },
    )


async def get_predictions_data(user_id: str) -> dict:
    """
    Pull data needed to run prediction engine:
    - 30-day mood/sleep/focus trend
    - Habit streaks and gaps
    - Goal progress
    """
    try:
        trend = await run_query(
            """MATCH (u:User {id:$userId})-[:LOGGED]->(m:Mood)
               WHERE m.date >= date() - duration({days:30})
               OPTIONAL MATCH (u)-[:SLEPT]->(s:Sleep {date:m.date})
               OPTIONAL MATCH (u)-[:FOCUSED]->(f:FocusSession {date:m.date})
               OPTIONAL MATCH (u)-[:EXERCISED]->(e:Exercise {date:m.date})
               RETURN toString(m.date) AS date,
                      m.score AS moodScore,
                      coalesce(s.hours, 0) AS sleepHours,
                      coalesce(f.hours, 0) AS focusHours,
                      e IS NOT NULL        AS exercised
               ORDER BY m.date ASC""",
            {"userId": user_id},
        )
        habit_gaps = await run_query(
            """MATCH (u:User {id:$userId})-[:TRACKED]->(h:Habit)
               WHERE h.lastDone < toString(date() - duration({days:3}))
               RETURN h.name AS name, h.streak AS streak
               ORDER BY h.streak DESC""",
            {"userId": user_id},
        )
        missed_goals = await run_query(
            """MATCH (u:User {id:$userId})-[:HAS_GOAL]->(g:Goal)
               WHERE g.progress < 10 AND g.createdAt < $cutoff
               RETURN g.title AS title, g.progress AS progress, g.category AS category""",
            {
                "userId": user_id,
                "cutoff": (datetime.now(timezone.utc).replace(day=1)).isoformat(),
            },
        )
        return {"trend": trend, "habitGaps": habit_gaps, "missedGoals": missed_goals}
    except Exception as exc:
        logger.warning(f"get_predictions_data error: {exc}")
        return {"trend": [], "habitGaps": [], "missedGoals": []}


# ── Graph Data ─────────────────────────────────────────────────────────────────

async def get_graph_data(user_id: str) -> dict:
    """
    Return a graph that actually looks like a knowledge graph:
    - ALL semantic nodes (Goals, Skills, Habits, Concepts, People) — named, meaningful
    - Only 6 most-recent of each daily-log type (Mood/Sleep/Exercise/FocusSession/BurnoutRisk)
    - Concept→Concept causal edges so the graph has cross-links, not just a hub-and-spoke star
    """
    try:
        # 1. All named / semantic nodes — these are what make the graph interesting
        named = await run_query(
            """MATCH (u:User {id:$userId})-[r]->(n)
               WHERE labels(n)[0] IN ['Goal','Skill','Habit','Person','Concept','Environment']
               RETURN labels(n)[0] AS type,
                      coalesce(n.id, n.name, n.title, toString(n.date)) AS id,
                      n.name AS name, n.title AS title,
                      toString(n.date) AS date, type(r) AS relationship""",
            {"userId": user_id},
        )

        # 2. Recent daily-log nodes — capped at 6 per type so they don't flood the graph
        daily = []
        for dtype in ["Mood", "Sleep", "Exercise", "FocusSession", "BurnoutRisk"]:
            rows = await run_query(
                f"""MATCH (u:User {{id:$userId}})-[r]->(n:{dtype})
                    RETURN labels(n)[0] AS type,
                           coalesce(n.id, toString(n.date)) AS id,
                           n.name AS name, n.title AS title,
                           toString(n.date) AS date, type(r) AS relationship
                    ORDER BY n.date DESC LIMIT 6""",
                {"userId": user_id},
            )
            daily.extend(rows)

        # 3. Concept→Concept causal edges (makes the graph a real graph, not a star)
        causal = await run_query(
            """MATCH (u:User {id:$userId})-[:HAS_CONCEPT]->(s:Concept)-[r]->(t:Concept)
               WHERE (u)-[:HAS_CONCEPT]->(t)
               RETURN s.name AS source, t.name AS target, type(r) AS rel""",
            {"userId": user_id},
        )

        # 4. Goal→Habit dependency edges
        goal_habit = await run_query(
            """MATCH (g:Goal {userId:$userId})-[r]->(h:Habit {userId:$userId})
               RETURN coalesce(g.id, g.title) AS source,
                      coalesce(h.id, h.name)  AS target,
                      type(r) AS rel""",
            {"userId": user_id},
        )

        # Build deduplicated node + link lists
        nodes, links = [], []
        seen_ids: set = set()
        user_node = {"id": user_id, "type": "User", "label": "You"}
        nodes.append(user_node)
        seen_ids.add(user_id)

        for row in named + daily:
            nid   = str(row.get("id") or row.get("name") or row.get("title") or row.get("date") or "?")
            label = row.get("name") or row.get("title") or row.get("type", "Node")
            if nid not in seen_ids:
                nodes.append({"id": nid, "type": row.get("type", "Node"), "label": label})
                seen_ids.add(nid)
            links.append({"source": user_id, "target": nid,
                          "type": row.get("relationship", "LINKED")})

        # Add cross-node edges
        for c in causal + goal_habit:
            src, tgt = str(c.get("source", "")), str(c.get("target", ""))
            if src and tgt and src in seen_ids and tgt in seen_ids:
                links.append({"source": src, "target": tgt, "type": c.get("rel", "LINKED")})

        return {"nodes": nodes, "links": links}
    except Exception as exc:
        logger.warning(f"get_graph_data error: {exc}")
        return {"nodes": [], "links": [], "offline": True}


# ── Weekly Insights ────────────────────────────────────────────────────────────

async def get_weekly_insights(user_id: str) -> dict:
    try:
        weekly = await run_query(
            """MATCH (u:User {id:$userId})-[:LOGGED]->(m:Mood)
               WHERE m.date >= date() - duration({days:7})
               OPTIONAL MATCH (u)-[:SLEPT]->(s:Sleep {date:m.date})
               OPTIONAL MATCH (u)-[:FOCUSED]->(f:FocusSession {date:m.date})
               OPTIONAL MATCH (u)-[:HAS_BURNOUT_RISK]->(b:BurnoutRisk {date:m.date})
               RETURN toString(m.date) AS date,
                      m.score AS moodScore,
                      coalesce(s.hours, 0)      AS sleepHours,
                      coalesce(f.hours, 0)      AS focusHours,
                      coalesce(b.score, 0)      AS burnoutScore
               ORDER BY m.date ASC""",
            {"userId": user_id},
        )
        habits_rows = await run_query(
            """MATCH (u:User {id:$userId})-[:TRACKED]->(h:Habit)
               WITH DISTINCT h
               RETURN h.name AS name, h.streak AS streak ORDER BY h.streak DESC LIMIT 5""",
            {"userId": user_id},
        )
        goals_rows = await run_query(
            """MATCH (u:User {id:$userId})-[:HAS_GOAL]->(g:Goal)
               RETURN g.title AS title, g.progress AS progress, g.category AS category""",
            {"userId": user_id},
        )
        return {
            "weeklyData": weekly,
            "topHabits":  habits_rows,
            "goals":      goals_rows,
        }
    except Exception as exc:
        logger.warning(f"get_weekly_insights error: {exc}")
        return {"weeklyData": [], "topHabits": [], "goals": []}


# ── Causal Patterns ────────────────────────────────────────────────────────────

async def detect_causal_patterns(user_id: str) -> dict:
    try:
        sleep_mood = await run_query(
            """MATCH (u:User {id:$userId})-[:SLEPT]->(s:Sleep),
                     (u)-[:LOGGED]->(m:Mood {date:s.date})
               WHERE s.hours >= 7
               RETURN avg(m.score) AS avgMoodWithGoodSleep,
                      avg(s.hours) AS avgSleepHours""",
            {"userId": user_id},
        )
        ex_prod = await run_query(
            """MATCH (u:User {id:$userId})-[:EXERCISED]->(e:Exercise),
                     (u)-[:FOCUSED]->(f:FocusSession {date:e.date})
               RETURN avg(f.hours) AS avgFocusWithExercise""",
            {"userId": user_id},
        )
        no_ex = await run_query(
            """MATCH (u:User {id:$userId})-[:FOCUSED]->(f:FocusSession)
               WHERE NOT EXISTS {
                 MATCH (u)-[:EXERCISED]->(e:Exercise {date:f.date})
               }
               RETURN avg(f.hours) AS avgFocusWithoutExercise""",
            {"userId": user_id},
        )
        return {
            "sleepMood": sleep_mood[0] if sleep_mood else {},
            "exerciseProductivity": {
                "withExercise":    (ex_prod[0] or {}).get("avgFocusWithExercise", 0),
                "withoutExercise": (no_ex[0] or {}).get("avgFocusWithoutExercise", 0),
            },
        }
    except Exception as exc:
        logger.warning(f"detect_causal_patterns error: {exc}")
        return {"sleepMood": {}, "exerciseProductivity": {}}


# ── Timeline & History ─────────────────────────────────────────────────────────

async def get_timeline(user_id: str, days: int = 30) -> list[dict]:
    try:
        return await run_query(
            """MATCH (u:User {id:$userId})-[:LOGGED]->(m:Mood)
               WHERE m.date >= date() - duration({days:$days})
               OPTIONAL MATCH (u)-[:HAS_BURNOUT_RISK]->(b:BurnoutRisk {date:m.date})
               RETURN toString(m.date) AS date, m.score AS moodScore,
                      m.emotion AS emotion, b.score AS burnoutScore
               ORDER BY m.date ASC""",
            {"userId": user_id, "days": days},
        )
    except Exception:
        return []


async def get_user_history(user_id: str, limit: int = 10) -> list[dict]:
    try:
        return await run_query(
            """MATCH (u:User {id:$userId})-[:LOGGED]->(m:Mood)
               RETURN toString(m.date) AS date, m.score AS moodScore,
                      m.notes AS notes, m.emotion AS emotion, m.language AS language
               ORDER BY m.date DESC LIMIT $limit""",
            {"userId": user_id, "limit": limit},
        )
    except Exception:
        return []
