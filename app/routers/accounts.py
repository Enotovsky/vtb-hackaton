from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import ApiError, MemberCtx, active_membership, current_user, require_member
from app.db import get_db
from app.models import Account, Card, Family, Operation, Payment, User
from app.payments import PaymentRequest, accessible_account, execute_payment
from app.serializers import account_out, operation_out, payment_out

router = APIRouter(prefix="/api", tags=["accounts & payments"])


def _ctx_or_none(db: Session, user: User) -> MemberCtx | None:
    m = active_membership(db, user.id)
    return MemberCtx(user=user, member=m, family=db.get(Family, m.family_id)) if m else None


@router.get("/accounts")
def list_accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Свои личные счета + общий счёт семьи. Личные счета партнёра сюда не попадают никогда."""
    ctx = _ctx_or_none(db, user)
    conds = [(Account.kind == "personal") & (Account.owner_user_id == user.id)]
    if ctx:
        conds.append((Account.kind == "shared") & (Account.family_id == ctx.family.id))
    out = []
    for a in db.scalars(select(Account).where(or_(*conds)).order_by(Account.id)):
        cards_q = select(Card).where(Card.account_id == a.id)
        if a.kind == "personal":
            cards_q = cards_q.where(Card.holder_user_id == user.id)
        out.append(account_out(db, a, db.scalars(cards_q).all()))
    return out


@router.get("/accounts/{account_id}/operations")
def account_operations(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acc = accessible_account(db, _ctx_or_none(db, user), user.id, account_id)
    ops = db.scalars(
        select(Operation).where(Operation.account_id == acc.id).order_by(Operation.id.desc()).limit(200)
    ).all()
    return [operation_out(db, o) for o in ops]


@router.get("/family/operations")
def family_operations(ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    """Общая история: операции общих счетов и кошельков детей. Операции по личным счетам не включаются."""
    ops = db.scalars(
        select(Operation).where(Operation.family_id == ctx.family.id).order_by(Operation.id.desc()).limit(200)
    ).all()
    return [operation_out(db, o) for o in ops]


class PaymentIn(BaseModel):
    account_id: int
    amount: int = Field(default=0, ge=0, description="копейки; для напоминания можно не указывать")
    purpose: str = ""
    child_id: int | None = None
    budget_item_id: int | None = None
    reminder_id: int | None = None
    simulate_failure: bool = Field(default=False, description="тест: имитировать отказ провайдера")


@router.post("/payments")
def create_payment(
    body: PaymentIn,
    idempotency_key: str | None = Header(default=None),
    ctx: MemberCtx = Depends(require_member),
    db: Session = Depends(get_db),
):
    p, replayed = execute_payment(db, ctx, PaymentRequest(
        kind="purchase", account_id=body.account_id, amount=body.amount, idempotency_key=idempotency_key or "",
        purpose=body.purpose, child_id=body.child_id, budget_item_id=body.budget_item_id,
        reminder_id=body.reminder_id, simulate_failure=body.simulate_failure,
    ))
    return {**payment_out(db, p), "replayed": replayed}


class TransferIn(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: int = Field(gt=0)


@router.post("/transfers")
def transfer(
    body: TransferIn,
    idempotency_key: str | None = Header(default=None),
    ctx: MemberCtx = Depends(require_member),
    db: Session = Depends(get_db),
):
    """Тестовый перевод между доступными счетами (например, личный -> общий)."""
    p, replayed = execute_payment(db, ctx, PaymentRequest(
        kind="shared_topup", account_id=body.from_account_id, target_account_id=body.to_account_id,
        amount=body.amount, idempotency_key=idempotency_key or "", purpose="Пополнение счёта",
    ))
    return {**payment_out(db, p), "replayed": replayed}


def _visible_payments_query(ctx: MemberCtx):
    shared_ids = select(Account.id).where(Account.family_id == ctx.family.id)
    return select(Payment).where(
        or_(Payment.payer_user_id == ctx.user.id, Payment.account_id.in_(shared_ids))
    )


@router.get("/payments")
def list_payments(ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    ps = db.scalars(_visible_payments_query(ctx).order_by(Payment.id.desc()).limit(200)).all()
    return [payment_out(db, p) for p in ps]


@router.get("/payments/{payment_id}")
def get_payment(payment_id: int, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    p = db.scalar(_visible_payments_query(ctx).where(Payment.id == payment_id))
    if not p:
        raise ApiError(404, "payment_not_found", "Платёж не найден")
    return payment_out(db, p)
