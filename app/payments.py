"""Проведение тестовых платежей.

Гарантии:
* идемпотентность — (плательщик, Idempotency-Key) уникальны; повтор возвращает тот же платёж, деньги не списываются повторно;
* атомарность — проверка остатка и списание одним UPDATE ... WHERE balance >= amount внутри одной транзакции;
  при любой ошибке транзакция откатывается и деньги не меняются, платёж получает статус failed;
* статусы — pending (записан, ещё проводится) -> completed | failed.
"""
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import ApiError, MemberCtx, log_event, request_hash
from app.models import (
    Account,
    BudgetItem,
    BudgetPlan,
    Card,
    Child,
    Operation,
    Payment,
    Reminder,
    utcnow,
)


class PaymentFailed(Exception):
    def __init__(self, reason: str):
        self.reason = reason


@dataclass
class PaymentRequest:
    kind: str  # purchase | child_topup | shared_topup
    account_id: int
    amount: int
    idempotency_key: str
    purpose: str
    child_id: int | None = None
    budget_item_id: int | None = None
    reminder_id: int | None = None
    target_account_id: int | None = None
    simulate_failure: bool = False


def accessible_account(db: Session, ctx: MemberCtx | None, user_id: int, account_id: int) -> Account:
    """Свой личный счёт или общий счёт своей семьи. Чужие личные счета — как будто их нет (404)."""
    acc = db.get(Account, account_id)
    ok = acc is not None and (
        (acc.kind == "personal" and acc.owner_user_id == user_id)
        or (acc.kind == "shared" and ctx is not None and acc.family_id == ctx.family.id)
    )
    if not ok:
        raise ApiError(404, "account_not_found", "Счёт не найден")
    return acc


def _payer_card(db: Session, account_id: int, user_id: int) -> int | None:
    return db.scalar(
        select(Card.id).where(Card.account_id == account_id, Card.holder_user_id == user_id, Card.status == "active")
    )


def _resolve_links(db: Session, ctx: MemberCtx, req: PaymentRequest):
    """Проверка ссылок на ребёнка / статью бюджета / напоминание + вывод недостающих полей."""
    if req.reminder_id:
        r = db.get(Reminder, req.reminder_id)
        if not r or r.family_id != ctx.family.id:
            raise ApiError(404, "reminder_not_found", "Напоминание не найдено")
        if r.status != "open":
            raise ApiError(409, "reminder_closed", f"Напоминание уже закрыто (статус «{r.status}»)")
        if req.amount and req.amount != r.amount:
            raise ApiError(400, "amount_mismatch", "Сумма не совпадает с напоминанием")
        req.amount = r.amount
        req.child_id = r.child_id
        req.budget_item_id = r.budget_item_id
        req.purpose = req.purpose or r.title
    if req.budget_item_id:
        item = db.get(BudgetItem, req.budget_item_id)
        plan = db.get(BudgetPlan, item.plan_id) if item else None
        if not plan or plan.family_id != ctx.family.id:
            raise ApiError(404, "budget_item_not_found", "Статья бюджета не найдена")
        if plan.status != "approved":
            raise ApiError(409, "plan_not_approved", "План ещё не согласован")
        req.child_id = plan.child_id
        req.purpose = req.purpose or item.title
    if req.child_id:
        c = db.get(Child, req.child_id)
        if not c or c.family_id != ctx.family.id:
            raise ApiError(404, "child_not_found", "Ребёнок не найден")
    if req.amount <= 0:
        raise ApiError(400, "bad_amount", "Сумма должна быть больше нуля")


def execute_payment(db: Session, ctx: MemberCtx, req: PaymentRequest) -> tuple[Payment, bool]:
    """Возвращает (платёж, replayed). replayed=True — это повтор уже известного запроса."""
    user_id = ctx.user.id
    if not req.idempotency_key:
        raise ApiError(400, "idempotency_key_required", "Нужен заголовок Idempotency-Key")

    existing = db.scalar(
        select(Payment).where(Payment.payer_user_id == user_id, Payment.idempotency_key == req.idempotency_key)
    )
    if existing:
        return _replay(existing, req), True

    accessible_account(db, ctx, user_id, req.account_id)
    _resolve_links(db, ctx, req)
    if req.target_account_id:
        accessible_account(db, ctx, user_id, req.target_account_id)
        if req.target_account_id == req.account_id:
            raise ApiError(400, "same_account", "Счёт списания и зачисления совпадают")

    h = request_hash({k: v for k, v in req.__dict__.items() if k != "idempotency_key"})
    payment = Payment(
        idempotency_key=req.idempotency_key, kind=req.kind, family_id=ctx.family.id, account_id=req.account_id,
        target_account_id=req.target_account_id, payer_user_id=user_id, child_id=req.child_id,
        budget_item_id=req.budget_item_id, reminder_id=req.reminder_id, amount=req.amount,
        purpose=req.purpose or "Тестовая оплата", status="pending", request_hash=h,
    )
    db.add(payment)
    try:
        db.commit()  # фиксируем pending: параллельный дубль упрётся в уникальный ключ
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Payment).where(Payment.payer_user_id == user_id, Payment.idempotency_key == req.idempotency_key)
        )
        return _replay(existing, req), True

    try:
        if req.simulate_failure:
            raise PaymentFailed("provider_error")
        _apply_money(db, ctx, payment)
        payment.status, payment.finished_at = "completed", utcnow()
        log_event(db, "payment_completed", family_id=ctx.family.id, user_id=user_id, payment_id=payment.id,
                  kind=payment.kind, amount=payment.amount, child_id=payment.child_id)
        db.commit()
    except Exception as e:  # noqa: BLE001 — любая ошибка = откат денег
        db.rollback()
        reason = e.reason if isinstance(e, PaymentFailed) else "internal_error"
        payment = db.get(Payment, payment.id)
        payment.status, payment.failure_reason, payment.finished_at = "failed", reason, utcnow()
        log_event(db, "payment_failed", family_id=ctx.family.id, user_id=user_id, payment_id=payment.id,
                  kind=payment.kind, amount=payment.amount, reason=reason)
        db.commit()
    return payment, False


def _replay(existing: Payment, req: PaymentRequest) -> Payment:
    check = PaymentRequest(**{**req.__dict__})
    # сравниваем только явно переданные клиентом параметры
    if existing.account_id != check.account_id or existing.kind != check.kind or (
        check.amount and check.amount != existing.amount
    ):
        raise ApiError(409, "idempotency_conflict", "Этот Idempotency-Key уже использован для другого платежа")
    return existing


def _debit(db: Session, account_id: int, amount: int) -> int:
    res = db.execute(
        update(Account).where(Account.id == account_id, Account.balance >= amount)
        .values(balance=Account.balance - amount)
    )
    if res.rowcount == 0:
        raise PaymentFailed("insufficient_funds")
    return db.scalar(select(Account.balance).where(Account.id == account_id))


def _apply_money(db: Session, ctx: MemberCtx, p: Payment):
    src = db.get(Account, p.account_id)
    fam_of = lambda acc: acc.family_id if acc.kind == "shared" else None  # noqa: E731

    if p.reminder_id:
        res = db.execute(
            update(Reminder).where(Reminder.id == p.reminder_id, Reminder.status == "open")
            .values(status="paid", payment_id=p.id, closed_at=utcnow(), closed_by=p.payer_user_id)
        )
        if res.rowcount == 0:
            raise PaymentFailed("reminder_closed")

    bal = _debit(db, p.account_id, p.amount)
    op_type = {"purchase": "payment", "child_topup": "child_topup", "shared_topup": "transfer_out"}[p.kind]
    debit_op = Operation(
        family_id=fam_of(src), account_id=src.id, card_id=_payer_card(db, src.id, p.payer_user_id),
        amount=-p.amount, balance_after=bal, type=op_type, payer_user_id=p.payer_user_id,
        child_id=p.child_id, purpose=p.purpose, payment_id=p.id,
    )
    db.add(debit_op)

    if p.kind == "child_topup":
        db.execute(update(Child).where(Child.id == p.child_id).values(balance=Child.balance + p.amount))
        child = db.get(Child, p.child_id)
        db.refresh(child)
        db.add(Operation(
            family_id=ctx.family.id, child_wallet_id=child.id, amount=p.amount, balance_after=child.balance,
            type="wallet_topup", payer_user_id=p.payer_user_id, child_id=child.id,
            purpose=p.purpose, payment_id=p.id,
        ))
    elif p.kind == "shared_topup":
        db.execute(update(Account).where(Account.id == p.target_account_id)
                   .values(balance=Account.balance + p.amount))
        dst = db.get(Account, p.target_account_id)
        db.refresh(dst)
        db.add(Operation(
            family_id=fam_of(dst), account_id=dst.id, amount=p.amount, balance_after=dst.balance,
            type="transfer_in", payer_user_id=p.payer_user_id, purpose=p.purpose, payment_id=p.id,
        ))

    db.flush()
    if p.reminder_id:
        db.execute(update(Reminder).where(Reminder.id == p.reminder_id).values(operation_id=debit_op.id))
        log_event(db, "reminder_closed", family_id=ctx.family.id, user_id=p.payer_user_id,
                  reminder_id=p.reminder_id, via="payment", payment_id=p.id)
