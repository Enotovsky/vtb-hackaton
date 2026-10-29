"""Преобразование моделей в JSON. Отдельно — чтобы не утекали лишние поля (личные счета партнёра и т.п.)."""
from sqlalchemy.orm import Session

from app.models import (
    Account,
    BudgetPlan,
    Card,
    Child,
    ChildPurchase,
    Invitation,
    Operation,
    Payment,
    Reminder,
    User,
)


def user_name(db: Session, user_id: int | None) -> str | None:
    if not user_id:
        return None
    u = db.get(User, user_id)
    return u.name if u else None


def child_name(db: Session, child_id: int | None) -> str | None:
    if not child_id:
        return None
    c = db.get(Child, child_id)
    return c.name if c else None


def card_out(db: Session, c: Card) -> dict:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "pan_masked": c.pan_masked,
        "holder": user_name(db, c.holder_user_id),
        "holder_user_id": c.holder_user_id,
        "status": c.status,
    }


def account_out(db: Session, a: Account, cards: list[Card]) -> dict:
    return {
        "id": a.id,
        "kind": a.kind,
        "name": a.name,
        "balance": a.balance,
        "cards": [card_out(db, c) for c in cards],
    }


def operation_out(db: Session, o: Operation) -> dict:
    card = db.get(Card, o.card_id) if o.card_id else None
    return {
        "id": o.id,
        "date": o.created_at.isoformat(),
        "amount": o.amount,
        "balance_after": o.balance_after,
        "type": o.type,
        "payer": user_name(db, o.payer_user_id) or child_name(db, o.payer_child_id),
        "payer_user_id": o.payer_user_id,
        "child_id": o.child_id,
        "child": child_name(db, o.child_id),
        "purpose": o.purpose,
        "account_id": o.account_id,
        "card": card.pan_masked if card else None,
        "payment_id": o.payment_id,
    }


def invitation_out(db: Session, i: Invitation, with_token: bool = False) -> dict:
    d = {
        "id": i.id,
        "family_id": i.family_id,
        "inviter": user_name(db, i.inviter_id),
        "invitee_email": i.invitee_email,
        "status": i.status,
        "created_at": i.created_at.isoformat(),
        "expires_at": i.expires_at.isoformat(),
        "responded_at": i.responded_at.isoformat() if i.responded_at else None,
    }
    if with_token:
        d["token"] = i.token
    return d


def child_out(c: Child, spent_this_month: int, with_code: bool = False) -> dict:
    d = {
        "id": c.id,
        "name": c.name,
        "birth_date": c.birth_date.isoformat() if c.birth_date else None,
        "balance": c.balance,
        "monthly_limit": c.monthly_limit,
        "spent_this_month": spent_this_month,
        "limit_left": max(c.monthly_limit - spent_this_month, 0),
        "frozen": c.frozen,
    }
    if with_code:
        d["access_code"] = c.access_code
    return d


def plan_out(db: Session, p: BudgetPlan) -> dict:
    return {
        "id": p.id,
        "child_id": p.child_id,
        "child": child_name(db, p.child_id),
        "month": p.month,
        "status": p.status,
        "version": p.version,
        "total": sum(i.amount for i in p.items),
        "created_by": user_name(db, p.created_by),
        "updated_by": user_name(db, p.updated_by),
        "submitted_by": user_name(db, p.submitted_by),
        "submitted_by_id": p.submitted_by,
        "approved_by": user_name(db, p.approved_by),
        "approved_at": p.approved_at.isoformat() if p.approved_at else None,
        "review_comment": p.review_comment,
        "updated_at": p.updated_at.isoformat(),
        "items": [
            {
                "id": i.id,
                "category": i.category,
                "title": i.title,
                "amount": i.amount,
                "due_date": i.due_date.isoformat() if i.due_date else None,
                "payee_url": i.payee_url,
            }
            for i in p.items
        ],
    }


def payment_out(db: Session, p: Payment) -> dict:
    return {
        "id": p.id,
        "kind": p.kind,
        "status": p.status,
        "failure_reason": p.failure_reason,
        "amount": p.amount,
        "purpose": p.purpose,
        "account_id": p.account_id,
        "payer": user_name(db, p.payer_user_id),
        "payer_user_id": p.payer_user_id,
        "child_id": p.child_id,
        "child": child_name(db, p.child_id),
        "budget_item_id": p.budget_item_id,
        "reminder_id": p.reminder_id,
        "idempotency_key": p.idempotency_key,
        "created_at": p.created_at.isoformat(),
        "finished_at": p.finished_at.isoformat() if p.finished_at else None,
    }


def child_purchase_out(p: ChildPurchase) -> dict:
    return {
        "id": p.id,
        "amount": p.amount,
        "merchant": p.merchant,
        "status": p.status,
        "failure_reason": p.failure_reason,
        "created_at": p.created_at.isoformat(),
    }


def reminder_out(db: Session, r: Reminder) -> dict:
    return {
        "id": r.id,
        "child_id": r.child_id,
        "child": child_name(db, r.child_id),
        "budget_item_id": r.budget_item_id,
        "title": r.title,
        "amount": r.amount,
        "due_date": r.due_date.isoformat() if r.due_date else None,
        "payee_url": r.payee_url,
        "status": r.status,
        "payment_id": r.payment_id,
        "operation_id": r.operation_id,
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        "closed_by": user_name(db, r.closed_by),
    }
