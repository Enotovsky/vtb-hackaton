from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import (
    ApiError,
    MemberCtx,
    current_child,
    get_family_child,
    log_event,
    require_member,
    require_owner,
)
from app.db import get_db
from app.models import AuthSession, Child, ChildPurchase, Operation, utcnow
from app.payments import PaymentRequest, execute_payment
from app.routers.family import _new_child_code, current_month, spent_this_month
from app.serializers import child_out, child_purchase_out, operation_out, payment_out

router = APIRouter(prefix="/api", tags=["children"])


# ---------------- сторона родителя ----------------

@router.get("/children/{child_id}")
def get_child(child_id: int, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    c = get_family_child(db, ctx, child_id)
    return child_out(c, spent_this_month(db, c.id), with_code=ctx.is_owner)


class ChildSettingsIn(BaseModel):
    monthly_limit: int | None = Field(default=None, ge=0)
    frozen: bool | None = None


@router.patch("/children/{child_id}/settings")
def update_child_settings(
    child_id: int, body: ChildSettingsIn, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)
):
    """Лимит и заморозка. Изменение лимита НЕ меняет баланс — это только ограничение трат."""
    c = get_family_child(db, ctx, child_id)
    if body.monthly_limit is not None and body.monthly_limit != c.monthly_limit:
        log_event(db, "child_limit_changed", family_id=ctx.family.id, user_id=ctx.user.id, child_id=c.id,
                  old=c.monthly_limit, new=body.monthly_limit)
        c.monthly_limit = body.monthly_limit
    if body.frozen is not None and body.frozen != c.frozen:
        log_event(db, "child_frozen" if body.frozen else "child_unfrozen", family_id=ctx.family.id,
                  user_id=ctx.user.id, child_id=c.id)
        c.frozen = body.frozen
    db.commit()
    return child_out(c, spent_this_month(db, c.id), with_code=ctx.is_owner)


class TopupIn(BaseModel):
    account_id: int
    amount: int = Field(gt=0)


@router.post("/children/{child_id}/topup")
def topup_child(
    child_id: int,
    body: TopupIn,
    idempotency_key: str | None = Header(default=None),
    ctx: MemberCtx = Depends(require_member),
    db: Session = Depends(get_db),
):
    """Пополнение кошелька ребёнка со счёта родителя. Использованный лимит месяца не сбрасывается."""
    c = get_family_child(db, ctx, child_id)
    p, replayed = execute_payment(db, ctx, PaymentRequest(
        kind="child_topup", account_id=body.account_id, amount=body.amount,
        idempotency_key=idempotency_key or "", purpose=f"Пополнение кошелька: {c.name}", child_id=c.id,
    ))
    return {**payment_out(db, p), "replayed": replayed}


@router.post("/children/{child_id}/access-code")
def regenerate_code(child_id: int, ctx: MemberCtx = Depends(require_owner), db: Session = Depends(get_db)):
    """Новый код входа ребёнка; все старые сессии ребёнка отзываются."""
    c = get_family_child(db, ctx, child_id)
    c.access_code = _new_child_code()
    db.execute(update(AuthSession).where(AuthSession.child_id == c.id).values(revoked=True))
    db.commit()
    return {"access_code": c.access_code}


@router.get("/children/{child_id}/history")
def child_history(child_id: int, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    c = get_family_child(db, ctx, child_id)
    return _history(db, c)


def _history(db: Session, c: Child) -> dict:
    ops = db.scalars(
        select(Operation).where(Operation.child_wallet_id == c.id).order_by(Operation.id.desc()).limit(100)
    ).all()
    purchases = db.scalars(
        select(ChildPurchase).where(ChildPurchase.child_id == c.id).order_by(ChildPurchase.id.desc()).limit(100)
    ).all()
    return {"wallet_operations": [operation_out(db, o) for o in ops],
            "purchases": [child_purchase_out(p) for p in purchases]}


# ---------------- режим ребёнка (отдельное устройство) ----------------

@router.get("/child/me")
def child_me(child: Child = Depends(current_child), db: Session = Depends(get_db)):
    return {**child_out(child, spent_this_month(db, child.id)), **_history(db, child)}


class ChildPurchaseIn(BaseModel):
    amount: int = Field(gt=0)
    merchant: str = Field(default="Тестовый магазин", max_length=200)


class _Fail(Exception):
    def __init__(self, reason):
        self.reason = reason


@router.post("/child/purchases")
def child_purchase(
    body: ChildPurchaseIn,
    idempotency_key: str | None = Header(default=None),
    child: Child = Depends(current_child),
    db: Session = Depends(get_db),
):
    """Покупка ребёнка со своего устройства."""
    return purchase_from_wallet(db, child, body.amount, body.merchant, idempotency_key)


@router.post("/children/{child_id}/test-purchase")
def parent_test_purchase(
    child_id: int,
    body: ChildPurchaseIn,
    idempotency_key: str | None = Header(default=None),
    ctx: MemberCtx = Depends(require_member),
    db: Session = Depends(get_db),
):
    """Тест для родителя: та же покупка с кошелька ребёнка и те же серверные проверки, без входа по коду."""
    c = get_family_child(db, ctx, child_id)
    return purchase_from_wallet(db, c, body.amount, body.merchant, idempotency_key, by_user_id=ctx.user.id)


def purchase_from_wallet(db: Session, child: Child, amount: int, merchant: str, idempotency_key: str | None,
                         by_user_id: int | None = None) -> dict:
    """Списание с виртуального кошелька. Сервер проверяет заморозку, месячный лимит и баланс кошелька."""
    if not idempotency_key:
        raise ApiError(400, "idempotency_key_required", "Нужен заголовок Idempotency-Key")
    q = select(ChildPurchase).where(ChildPurchase.child_id == child.id,
                                    ChildPurchase.idempotency_key == idempotency_key)
    if existing := db.scalar(q):
        return {**child_purchase_out(existing), "replayed": True}

    purchase = ChildPurchase(child_id=child.id, idempotency_key=idempotency_key, amount=amount,
                             merchant=merchant, month=current_month())
    db.add(purchase)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {**child_purchase_out(db.scalar(q)), "replayed": True}

    actor = {"user_id": by_user_id} if by_user_id else {"actor_child_id": child.id}
    source = "parent_test" if by_user_id else "child_device"
    try:
        # Берём блокировку записи ребёнка ДО проверок, чтобы параллельные покупки шли строго по очереди
        # (в SQLite — через первую запись в транзакции, в PostgreSQL сработал бы SELECT ... FOR UPDATE).
        db.execute(update(Child).where(Child.id == child.id).values(balance=Child.balance))
        c = db.scalar(select(Child).where(Child.id == child.id).with_for_update().execution_options(
            populate_existing=True))
        if c.frozen:
            raise _Fail("wallet_frozen")
        if spent_this_month(db, c.id) + amount > c.monthly_limit:
            raise _Fail("limit_exceeded")
        if c.balance < amount:
            raise _Fail("insufficient_funds")
        c.balance -= amount
        purchase.status = "completed"
        db.add(Operation(
            family_id=c.family_id, child_wallet_id=c.id, amount=-amount, balance_after=c.balance,
            type="child_purchase", payer_child_id=c.id, child_id=c.id, purpose=merchant,
        ))
        log_event(db, "child_purchase_completed", family_id=c.family_id, child_id=c.id,
                  amount=amount, purchase_id=purchase.id, source=source, **actor)
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        reason = e.reason if isinstance(e, _Fail) else "internal_error"
        purchase = db.get(ChildPurchase, purchase.id)
        purchase.status, purchase.failure_reason = "failed", reason
        log_event(db, "child_purchase_failed", family_id=child.family_id, child_id=child.id,
                  amount=amount, reason=reason, purchase_id=purchase.id, source=source, **actor)
        db.commit()
    return {**child_purchase_out(purchase), "replayed": False, "at": utcnow().isoformat()}
