"""User registration and authentication routes."""
import re
import random
import string
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from app.middleware.auth import sign_token, hash_password, verify_password, require_auth
from app.services.neo4j_service import create_user, find_user_by_email, update_user_language, assign_user_team

router = APIRouter(prefix="/api/users", tags=["users"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterBody(BaseModel):
    name: str
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.lower().strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is required")
        return v


class LoginBody(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return v.lower().strip()


class LanguageBody(BaseModel):
    language: str


class TeamAssignBody(BaseModel):
    userId: str
    teamId: str
    role: Optional[str] = None


@router.post("/create", status_code=201)
async def register(body: RegisterBody):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    # Block duplicate registrations — never issue a JWT for an existing account
    try:
        existing = await find_user_by_email(body.email)
        if existing:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Please sign in.",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Neo4j offline — allow offline path below

    suffix  = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    user_id = f"user_{int(time.time())}_{suffix}"
    ph      = hash_password(body.password)

    try:
        user = await create_user(user_id, body.name, body.email, "user", ph)
    except Exception:
        # Neo4j offline graceful degradation — issue token for in-memory session
        user = {
            "id": user_id, "name": body.name,
            "email": body.email, "twinScore": 50, "role": "user", "offline": True,
        }

    if not user:
        raise HTTPException(status_code=500, detail="Failed to create user. Please try again.")

    user.pop("passwordHash", None)
    token = sign_token({"userId": user["id"], "email": user.get("email", ""), "role": user.get("role", "user")})
    return {"user": user, "token": token}


@router.post("/login")
async def login(body: LoginBody):
    user = None
    try:
        user = await find_user_by_email(body.email)
    except Exception:
        pass

    # Use identical message for "not found" and "wrong password" to prevent user enumeration
    auth_error = HTTPException(status_code=401, detail="Invalid email or password.")

    if not user:
        raise auth_error

    if not user.get("passwordHash"):
        raise HTTPException(
            status_code=401,
            detail="This account was created before password authentication was required. Please re-register.",
        )

    if not verify_password(body.password, user["passwordHash"]):
        raise auth_error

    user.pop("passwordHash", None)
    token = sign_token({"userId": user["id"], "email": user.get("email", ""), "role": user.get("role", "user")})
    return {"user": user, "token": token}


@router.patch("/language")
async def set_language(body: LanguageBody, current_user: dict = Depends(require_auth)):
    if not body.language:
        raise HTTPException(status_code=400, detail="language is required")
    await update_user_language(current_user["userId"], body.language)
    return {"success": True}


@router.patch("/team")
async def assign_team(body: TeamAssignBody, current_user: dict = Depends(require_auth)):
    """Assign a user to a team (self-service or manager). Required for Enterprise dashboard."""
    from app.middleware.auth import MANAGER_ROLES
    # Users can assign themselves; managers can assign any userId
    is_manager = current_user["role"].lower() in MANAGER_ROLES
    target_id = body.userId if is_manager else current_user["userId"]
    found = await assign_user_team(target_id, body.teamId, body.role if is_manager else None)
    if not found:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"success": True, "userId": target_id, "teamId": body.teamId}
