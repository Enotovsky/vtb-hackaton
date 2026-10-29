import secrets

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import (
    ApiError,
    active_membership,
    get_auth_session,
    hash_password,
    new_token,
    verify_password,
)
from app.db import get_db
from app.models import Account, AuthSession, Card, Child, Family, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

TEST_PERSONAL_BALANCE = 50_000_00  # 50 000 ₽ тестовых денег на личном счёте


def random_pan() -> str:
    return f"2200 •••• •••• {secrets.randbelow(10000):04d}"


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=4, max_length=100)
    device: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str
    device: str | None = None


class ChildLoginIn(BaseModel):
    code: str
    device: str | None = None


def _open_session(db: Session, user_id=None, child_id=None, device=None) -> str:
    token = new_token()
    db.add(AuthSession(token=token, user_id=user_id, child_id=child_id, device=device))
    return token


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise ApiError(409, "email_taken", "Пользователь с таким email уже есть")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    acc = Account(kind="personal", name="Личный счёт", owner_user_id=user.id, balance=TEST_PERSONAL_BALANCE)
    db.add(acc)
    db.flush()
    db.add(Card(account_id=acc.id, holder_user_id=user.id, pan_masked=random_pan()))
    token = _open_session(db, user_id=user.id, device=body.device)
    db.commit()
    return {"token": token, "user": {"id": user.id, "email": user.email, "name": user.name}}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "bad_credentials", "Неверный email или пароль")
    token = _open_session(db, user_id=user.id, device=body.device)
    db.commit()
    return {"token": token, "user": {"id": user.id, "email": user.email, "name": user.name}}


@router.post("/child-login")
def child_login(body: ChildLoginIn, db: Session = Depends(get_db)):
    child = db.scalar(select(Child).where(Child.access_code == body.code.strip().upper()))
    if not child:
        raise ApiError(401, "bad_code", "Неверный код ребёнка")
    token = _open_session(db, child_id=child.id, device=body.device)
    db.commit()
    return {"token": token, "child": {"id": child.id, "name": child.name}}


@router.post("/logout")
def logout(sess: AuthSession = Depends(get_auth_session), db: Session = Depends(get_db)):
    sess.revoked = True
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(sess: AuthSession = Depends(get_auth_session), db: Session = Depends(get_db)):
    if sess.child_id:
        c = db.get(Child, sess.child_id)
        return {"kind": "child", "child": {"id": c.id, "name": c.name, "family_id": c.family_id}}
    user = db.get(User, sess.user_id)
    m = active_membership(db, user.id)
    family = db.get(Family, m.family_id) if m else None
    return {
        "kind": "adult",
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "family": {"id": family.id, "name": family.name, "role": m.role} if family else None,
    }
