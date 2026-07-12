"""Neo4j database connection and query runner."""
import os
import logging
from neo4j import AsyncGraphDatabase, basic_auth
from neo4j.exceptions import ServiceUnavailable

logger = logging.getLogger(__name__)

NEO4J_URI      = os.getenv("NEO4J_URI", "")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE") or None   # None → AuraDB home database

_driver = None


def get_driver():
    global _driver
    if _driver is None and NEO4J_URI:
        _driver = AsyncGraphDatabase.driver(
            NEO4J_URI,
            auth=basic_auth(NEO4J_USERNAME, NEO4J_PASSWORD),
            max_connection_lifetime=3600,
        )
    return _driver


async def run_query(cypher: str, params: dict = None) -> list[dict]:
    """Execute a Cypher query and return rows as plain dicts."""
    driver = get_driver()
    if not driver:
        logger.warning("Neo4j not configured — returning empty result set")
        return []
    params = params or {}
    async with driver.session(database=NEO4J_DATABASE) as session:
        result = await session.run(cypher, params)
        records = await result.data()
    # Convert Neo4j integers / types to plain Python
    return [_sanitize(r) for r in records]


def _sanitize(record: dict) -> dict:
    out = {}
    for k, v in record.items():
        if v is None:
            out[k] = None
        elif hasattr(v, "value"):          # neo4j.graph.Node etc.
            out[k] = v.value
        elif hasattr(v, "__class__") and v.__class__.__name__ == "Integer":
            out[k] = int(v)
        elif hasattr(v, "__class__") and v.__class__.__name__ in ("Date", "DateTime", "Time", "LocalDateTime", "LocalTime"):
            # Neo4j native temporal types — convert to ISO string
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            # Python datetime/date objects
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


async def test_connection() -> bool:
    try:
        rows = await run_query("RETURN 1 AS ok")
        logger.info("✅ Neo4j connection verified")
        return True
    except Exception as exc:
        logger.warning(f"⚠️  Neo4j not reachable: {exc}")
        return False


async def init_schema():
    """Create constraints / indexes required by SynapseTwin."""
    constraints = [
        "CREATE CONSTRAINT user_email_unique IF NOT EXISTS FOR (u:User) REQUIRE u.email IS UNIQUE",
        "CREATE CONSTRAINT user_id_unique IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
        "CREATE INDEX mood_date IF NOT EXISTS FOR (m:Mood) ON (m.date)",
        "CREATE INDEX goal_title IF NOT EXISTS FOR (g:Goal) ON (g.title)",
    ]
    for cypher in constraints:
        try:
            await run_query(cypher)
        except Exception as exc:
            logger.debug(f"Schema init (non-fatal): {exc}")
