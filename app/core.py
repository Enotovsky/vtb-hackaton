"""Общие вещи: ошибки, пароли, авторизация, проверка прав, журнал событий."""
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuthSession, Child, Event, Family, FamilyMember, User


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(status_code=status, detail={"code": code, "message": message, **extra})


# ---------- пароли / токены ----------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, digest = stored.split("$", 1)
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(check.hex(), digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def request_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


# ---------- события ----------

def log_event(db: Session, type_: str, family_id=None, user_id=None, actor_child_id=None, **payload):
    db.add(Event(type=type_, family_id=family_id, actor_user_id=user_id, actor_child_id=actor_child_id, payload=payload))


# ---------- авторизация ----------

def get_auth_session(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> AuthSession:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "Требуется вход")
    token = authorization.split(" ", 1)[1].strip()
    sess = db.get(AuthSession, token)
    if not sess or sess.revoked:
        raise ApiError(401, "unauthorized", "Сессия недействительна, войдите заново")
    return sess


def current_user(sess: AuthSession = Depends(get_auth_session), db: Session = Depends(get_db)) -> User:
    if not sess.user_id:
        raise ApiError(403, "adult_only", "Действие доступно только взрослым")
    return db.get(User, sess.user_id)


def current_child(sess: AuthSession = Depends(get_auth_session), db: Session = Depends(get_db)) -> Child:
    if not sess.child_id:
        raise ApiError(403, "child_only", "Это режим ребёнка")
    child = db.get(Child, sess.child_id)
    if not child:
        raise ApiError(401, "unauthorized", "Профиль ребёнка не найден")
    return child


def active_membership(db: Session, user_id: int) -> FamilyMember | None:
    return db.scalar(
        select(FamilyMember).where(FamilyMember.user_id == user_id, FamilyMember.status == "active")
    )


@dataclass
class MemberCtx:
    user: User
    member: FamilyMember
    family: Family

    @property
    def is_owner(self) -> bool:
        return self.member.role == "owner"


def require_member(user: User = Depends(current_user), db: Session = Depends(get_db)) -> MemberCtx:
    """Проверяется на каждом запросе: после выхода/удаления из семьи доступ пропадает сразу."""
    m = active_membership(db, user.id)
    if not m:
        raise ApiError(403, "not_family_member", "Вы не состоите в семье")
    return MemberCtx(user=user, member=m, family=db.get(Family, m.family_id))


def require_owner(ctx: MemberCtx = Depends(require_member)) -> MemberCtx:
    if not ctx.is_owner:
        raise ApiError(403, "owner_only", "Только владелец семьи может управлять составом")
    return ctx


def get_family_child(db: Session, ctx: MemberCtx, child_id: int) -> Child:
    child = db.get(Child, child_id)
    if not child or child.family_id != ctx.family.id:
        raise ApiError(404, "child_not_found", "Ребёнок не найден")
    return child
