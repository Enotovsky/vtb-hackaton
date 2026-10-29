import secrets
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import (
    ApiError,
    MemberCtx,
    active_membership,
    current_user,
    log_event,
    new_token,
    require_member,
    require_owner,
)
from app.db import get_db
from app.models import (
    Account,
    Card,
    Child,
    ChildPurchase,
    Family,
    FamilyMember,
    Invitation,
    User,
    utcnow,
)
from app.routers.auth import random_pan
from app.serializers import child_out, invitation_out

router = APIRouter(prefix="/api", tags=["family"])

MAX_ADULTS = 2
DEFAULT_INVITE_TTL = 72 * 3600
TEST_SHARED_BALANCE = 30_000_00  # стартовый тестовый остаток общего счёта


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def spent_this_month(db: Session, child_id: int) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(ChildPurchase.amount), 0)).where(
            ChildPurchase.child_id == child_id,
            ChildPurchase.status == "completed",
            ChildPurchase.month == current_month(),
        )
    )


def expire_due_invitations(db: Session):
    """Ленивая отметка истёкших приглашений — вызывается перед любой работой с приглашениями."""
    now = utcnow()
    due = db.scalars(select(Invitation).where(Invitation.status == "sent", Invitation.expires_at <= now)).all()
    for inv in due:
        inv.status = "expired"
        inv.responded_at = now
        log_event(db, "invitation_expired", family_id=inv.family_id, invitation_id=inv.id)
    if due:
        db.commit()


def _issue_shared_card(db: Session, family_id: int, user_id: int):
    for acc in db.scalars(select(Account).where(Account.family_id == family_id, Account.kind == "shared")):
        card = db.scalar(select(Card).where(Card.account_id == acc.id, Card.holder_user_id == user_id))
        if card:
            card.status = "active"
        else:
            db.add(Card(account_id=acc.id, holder_user_id=user_id, pan_masked=random_pan()))


def _block_shared_cards(db: Session, family_id: int, user_id: int):
    acc_ids = select(Account.id).where(Account.family_id == family_id)
    db.execute(
        update(Card).where(Card.account_id.in_(acc_ids), Card.holder_user_id == user_id).values(status="blocked")
    )


# ---------------- семья ----------------

class FamilyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/family")
def create_family(body: FamilyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if active_membership(db, user.id):
        raise ApiError(409, "already_in_family", "Вы уже состоите в семье")
    fam = Family(name=body.name.strip(), owner_id=user.id)
    db.add(fam)
    db.flush()
    db.add(FamilyMember(family_id=fam.id, user_id=user.id, role="owner"))
    acc = Account(kind="shared", name="Общий счёт семьи", family_id=fam.id, balance=TEST_SHARED_BALANCE)
    db.add(acc)
    db.flush()
    db.add(Card(account_id=acc.id, holder_user_id=user.id, pan_masked=random_pan()))
    log_event(db, "family_created", family_id=fam.id, user_id=user.id, name=fam.name)
    db.commit()
    return {"id": fam.id, "name": fam.name}


@router.get("/family")
def get_family(ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    members = db.scalars(
        select(FamilyMember).where(FamilyMember.family_id == ctx.family.id, FamilyMember.status == "active")
    ).all()
    children = db.scalars(select(Child).where(Child.family_id == ctx.family.id)).all()
    return {
        "id": ctx.family.id,
        "name": ctx.family.name,
        "my_role": ctx.member.role,
        "owner_id": ctx.family.owner_id,
        "members": [
            {"user_id": m.user_id, "name": m.user.name, "email": m.user.email, "role": m.role,
             "joined_at": m.joined_at.isoformat()}
            for m in members
        ],
        "children": [child_out(c, spent_this_month(db, c.id), with_code=ctx.is_owner) for c in children],
    }


@router.delete("/family/members/{user_id}")
def remove_member(user_id: int, ctx: MemberCtx = Depends(require_owner), db: Session = Depends(get_db)):
    if user_id == ctx.user.id:
        raise ApiError(400, "cannot_remove_self", "Владелец не может удалить сам себя")
    m = db.scalar(
        select(FamilyMember).where(
            FamilyMember.family_id == ctx.family.id, FamilyMember.user_id == user_id, FamilyMember.status == "active"
        )
    )
    if not m:
        raise ApiError(404, "member_not_found", "Участник не найден")
    m.status, m.ended_at = "removed", utcnow()
    _block_shared_cards(db, ctx.family.id, user_id)
    log_event(db, "member_removed", family_id=ctx.family.id, user_id=ctx.user.id, removed_user_id=user_id)
    db.commit()
    return {"ok": True}


@router.post("/family/leave")
def leave_family(ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    if ctx.is_owner:
        raise ApiError(400, "owner_cannot_leave", "Владелец не может выйти из семьи")
    ctx.member.status, ctx.member.ended_at = "left", utcnow()
    _block_shared_cards(db, ctx.family.id, ctx.user.id)
    log_event(db, "member_left", family_id=ctx.family.id, user_id=ctx.user.id)
    db.commit()
    return {"ok": True}


# ---------------- приглашения (сторона владельца) ----------------

class InviteIn(BaseModel):
    email: str
    ttl_seconds: int | None = Field(default=None, ge=1, le=30 * 24 * 3600)  # короткий TTL — для тестов


def _active_adults(db: Session, family_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(FamilyMember).where(
            FamilyMember.family_id == family_id, FamilyMember.status == "active"
        )
    )


@router.post("/family/invitations")
def create_invitation(body: InviteIn, ctx: MemberCtx = Depends(require_owner), db: Session = Depends(get_db)):
    expire_due_invitations(db)
    email = body.email.strip().lower()
    if email == ctx.user.email:
        raise ApiError(400, "self_invite", "Нельзя пригласить самого себя")
    if _active_adults(db, ctx.family.id) >= MAX_ADULTS:
        raise ApiError(409, "family_full", f"В семье уже {MAX_ADULTS} взрослых")
    invitee = db.scalar(select(User).where(User.email == email))
    if invitee and (m := active_membership(db, invitee.id)) and m.family_id == ctx.family.id:
        raise ApiError(409, "already_member", "Этот человек уже в семье")
    pending = db.scalar(
        select(Invitation).where(
            Invitation.family_id == ctx.family.id, Invitation.invitee_email == email, Invitation.status == "sent"
        )
    )
    if pending:
        raise ApiError(409, "invite_pending", "Приглашение уже отправлено — отмените его, чтобы отправить новое")
    inv = Invitation(
        family_id=ctx.family.id,
        inviter_id=ctx.user.id,
        invitee_email=email,
        token=new_token(),
        expires_at=utcnow() + timedelta(seconds=body.ttl_seconds or DEFAULT_INVITE_TTL),
    )
    db.add(inv)
    db.flush()
    log_event(db, "invitation_sent", family_id=ctx.family.id, user_id=ctx.user.id, invitation_id=inv.id)
    db.commit()
    # В реальном продукте ссылка уходит письмом/пушем; в MVP возвращаем её владельцу.
    return invitation_out(db, inv, with_token=True)


@router.get("/family/invitations")
def list_family_invitations(ctx: MemberCtx = Depends(require_owner), db: Session = Depends(get_db)):
    expire_due_invitations(db)
    invs = db.scalars(
        select(Invitation).where(Invitation.family_id == ctx.family.id).order_by(Invitation.id.desc())
    ).all()
    return [invitation_out(db, i, with_token=i.status == "sent") for i in invs]


@router.post("/family/invitations/{inv_id}/cancel")
def cancel_invitation(inv_id: int, ctx: MemberCtx = Depends(require_owner), db: Session = Depends(get_db)):
    expire_due_invitations(db)
    inv = db.get(Invitation, inv_id)
    if not inv or inv.family_id != ctx.family.id:
        raise ApiError(404, "invite_not_found", "Приглашение не найдено")
    res = db.execute(
        update(Invitation).where(Invitation.id == inv_id, Invitation.status == "sent")
        .values(status="cancelled", responded_at=utcnow())
    )
    if res.rowcount == 0:
        db.rollback()
        raise ApiError(409, f"invite_{inv.status}", f"Нельзя отменить приглашение в статусе «{inv.status}»")
    log_event(db, "invitation_cancelled", family_id=ctx.family.id, user_id=ctx.user.id, invitation_id=inv_id)
    db.commit()
    db.refresh(inv)
    return invitation_out(db, inv)


# ---------------- приглашения (сторона приглашённого) ----------------

def _invite_for_user(db: Session, token: str, user: User) -> Invitation:
    expire_due_invitations(db)
    inv = db.scalar(select(Invitation).where(Invitation.token == token))
    if not inv or inv.invitee_email != user.email:
        # не раскрываем чужие приглашения
        raise ApiError(404, "invite_not_found", "Приглашение не найдено")
    return inv


@router.get("/invitations/incoming")
def incoming_invitations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    expire_due_invitations(db)
    invs = db.scalars(
        select(Invitation).where(Invitation.invitee_email == user.email).order_by(Invitation.id.desc())
    ).all()
    out = []
    for i in invs:
        d = invitation_out(db, i, with_token=True)
        d["family_name"] = db.get(Family, i.family_id).name
        out.append(d)
    return out


@router.post("/invitations/{token}/accept")
def accept_invitation(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = _invite_for_user(db, token, user)

    # Повторное принятие: возвращаем тот же результат, второго участника не создаём.
    if inv.status == "accepted" and inv.accepted_user_id == user.id:
        m = active_membership(db, user.id)
        if m and m.family_id == inv.family_id:
            return {"status": "accepted", "family_id": inv.family_id, "already": True}
        # участника удалили / он вышел — старое приглашение доступ не возвращает
        raise ApiError(409, "invite_used", "Приглашение уже использовано. Попросите владельца отправить новое.")
    if inv.status != "sent":
        raise ApiError(409, f"invite_{inv.status}", f"Приглашение в статусе «{inv.status}» — принять нельзя")

    current = active_membership(db, user.id)
    if current and current.family_id != inv.family_id:
        raise ApiError(409, "already_in_family", "Вы уже состоите в другой семье")
    if not current and _active_adults(db, inv.family_id) >= MAX_ADULTS:
        raise ApiError(409, "family_full", "В семье уже максимум взрослых")

    # Атомарный переход sent -> accepted: из двух одновременных запросов пройдёт только один.
    res = db.execute(
        update(Invitation).where(Invitation.id == inv.id, Invitation.status == "sent")
        .values(status="accepted", responded_at=utcnow(), accepted_user_id=user.id)
    )
    if res.rowcount == 0:
        db.rollback()
        db.refresh(inv)
        if inv.status == "accepted" and inv.accepted_user_id == user.id:
            return {"status": "accepted", "family_id": inv.family_id, "already": True}
        raise ApiError(409, f"invite_{inv.status}", "Приглашение уже обработано")

    member = db.scalar(
        select(FamilyMember).where(FamilyMember.family_id == inv.family_id, FamilyMember.user_id == user.id)
    )
    if member:  # ранее выходил/был удалён — возвращаем ту же запись
        member.status, member.role, member.joined_at, member.ended_at = "active", "adult", utcnow(), None
    else:
        db.add(FamilyMember(family_id=inv.family_id, user_id=user.id, role="adult"))
    _issue_shared_card(db, inv.family_id, user.id)
    log_event(db, "invitation_accepted", family_id=inv.family_id, user_id=user.id, invitation_id=inv.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "accepted", "family_id": inv.family_id, "already": True}
    return {"status": "accepted", "family_id": inv.family_id, "already": False}


@router.post("/invitations/{token}/decline")
def decline_invitation(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = _invite_for_user(db, token, user)
    if inv.status == "declined":
        return {"status": "declined", "already": True}
    res = db.execute(
        update(Invitation).where(Invitation.id == inv.id, Invitation.status == "sent")
        .values(status="declined", responded_at=utcnow())
    )
    if res.rowcount == 0:
        db.rollback()
        raise ApiError(409, f"invite_{inv.status}", f"Приглашение в статусе «{inv.status}»")
    log_event(db, "invitation_declined", family_id=inv.family_id, user_id=user.id, invitation_id=inv.id)
    db.commit()
    return {"status": "declined", "already": False}


# ---------------- дети (состав семьи — владелец) ----------------

class ChildIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    birth_date: date | None = None
    monthly_limit: int = Field(default=0, ge=0)


def _new_child_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


@router.post("/family/children")
def add_child(body: ChildIn, ctx: MemberCtx = Depends(require_owner), db: Session = Depends(get_db)):
    c = Child(
        family_id=ctx.family.id, name=body.name.strip(), birth_date=body.birth_date,
        monthly_limit=body.monthly_limit, access_code=_new_child_code(),
    )
    db.add(c)
    db.flush()
    log_event(db, "child_added", family_id=ctx.family.id, user_id=ctx.user.id, child_id=c.id)
    db.commit()
    return child_out(c, 0, with_code=True)
