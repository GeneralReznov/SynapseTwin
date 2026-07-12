"""JWT authentication helpers for FastAPI."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Header, HTTPException, status
import jwt as pyjwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

JWT_SECRET    = os.getenv("SESSION_SECRET") or "synapsetwin-dev-secret-change-in-prod"
if not os.getenv("SESSION_SECRET"):
    import logging as _logging
    _logging.getLogger("synapsetwin.auth").warning(
        "⚠️  SESSION_SECRET is not set — using insecure default. Set it in Replit Secrets."
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

MANAGER_ROLES = {"manager", "admin", "hr", "director"}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Token helpers ──────────────────────────────────────────────────────────────

def sign_token(payload: dict, expires_days: int = JWT_EXPIRE_DAYS) -> str:
    data = payload.copy()
    data["exp"] = datetime.now(timezone.utc) + timedelta(days=expires_days)
    return pyjwt.encode(data, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ── Password helpers ───────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── FastAPI dependency — any authenticated user ────────────────────────────────

def _extract_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer <token> header is required.",
        )
    return authorization[7:]


async def require_auth(authorization: Optional[str] = Header(default=None)) -> dict:
    token = _extract_bearer(authorization)
    try:
        payload = verify_token(token)
        return {
            "userId": payload["userId"],
            "email":  payload.get("email", ""),
            "role":   payload.get("role", "user"),
        }
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired. Please log in again.",
        )
    except Exception as exc:
        import logging as _log
        _log.getLogger("synapsetwin.auth").error(f"Token verification error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication error. Please try again.",
        )


# ── FastAPI dependency — manager / admin only ──────────────────────────────────

async def require_manager(authorization: Optional[str] = Header(default=None)) -> dict:
    user = await require_auth(authorization)
    if user["role"].lower() not in MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Enterprise analytics are restricted to managers and HR.",
        )
    return user
