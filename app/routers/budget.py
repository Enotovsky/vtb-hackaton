"""Бюджет ребёнка по месяцам + напоминания об оплате.

* План ничего не резервирует — это только договорённость родителей, балансы не трогаются.
* Правки защищены версией (оптимистичная блокировка): кто сохранил с устаревшей version — получает 409.
* Изменение сумм в согласованном/отправленном плане возвращает его в черновик — нужно новое согласование.
"""
import re
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import ApiError, MemberCtx, get_family_child, log_event, require_member
from app.db import get_db
from app.models import BudgetItem, BudgetPlan, FamilyMember, Payment, Reminder, utcnow
from app.serializers import plan_out, reminder_out

router = APIRouter(prefix="/api", tags=["budget & reminders"])

CATEGORIES = {"section", "club", "pocket_money", "other"}


class ItemIn(BaseModel):
    id: int | None = None
    category: str
    title: str = Field(min_length=1, max_length=200)
    amount: int = Field(gt=0)
    due_date: date | None = None
    payee_url: str | None = None

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v not in CATEGORIES:
            raise ValueError(f"category must be one of {sorted(CATEGORIES)}")
        return v


class PlanCreateIn(BaseModel):
    month: str
    items: list[ItemIn] = []

    @field_validator("month")
    @classmethod
    def _month(cls, v):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
            raise ValueError("month must be YYYY-MM")
        return v


class PlanUpdateIn(BaseModel):
    version: int
    items: list[ItemIn]


class VersionIn(BaseModel):
    version: int
    comment: str | None = None


def _get_plan(db: Session, ctx: MemberCtx, plan_id: int) -> BudgetPlan:
    p = db.get(BudgetPlan, plan_id)
    if not p or p.family_id != ctx.family.id:
        raise ApiError(404, "plan_not_found", "План не найден")
    return p


def _bump_version(db: Session, plan: BudgetPlan, expected: int, **values):
    """Атомарно: меняем план, только если version не изменилась с момента, как клиент её видел."""
    res = db.execute(
        update(BudgetPlan).where(BudgetPlan.id == plan.id, BudgetPlan.version == expected)
        .values(version=BudgetPlan.version + 1, updated_at=utcnow(), **values)
    )
    if res.rowcount == 0:
        db.rollback()
        db.refresh(plan)
        raise ApiError(409, "version_conflict",
                       "План уже изменил другой участник. Обновите и повторите правку.",
                       current=plan_out(db, plan))


@router.get("/children/{child_id}/plans")
def list_plans(child_id: int, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    get_family_child(db, ctx, child_id)
    plans = db.scalars(select(BudgetPlan).where(BudgetPlan.child_id == child_id).order_by(BudgetPlan.month)).all()
    return [plan_out(db, p) for p in plans]


@router.post("/children/{child_id}/plans")
def create_plan(child_id: int, body: PlanCreateIn, ctx: MemberCtx = Depends(require_member),
                db: Session = Depends(get_db)):
    get_family_child(db, ctx, child_id)
    p = BudgetPlan(family_id=ctx.family.id, child_id=child_id, month=body.month,
                   created_by=ctx.user.id, updated_by=ctx.user.id)
    p.items = [BudgetItem(**i.model_dump(exclude={"id"})) for i in body.items]
    db.add(p)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "plan_exists", "План на этот месяц уже есть")
    log_event(db, "plan_created", family_id=ctx.family.id, user_id=ctx.user.id, plan_id=p.id, month=p.month)
    db.commit()
    return plan_out(db, p)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    return plan_out(db, _get_plan(db, ctx, plan_id))


def _item_is_paid(db: Session, item_id: int) -> bool:
    return bool(db.scalar(select(func.count()).select_from(Reminder).where(
        Reminder.budget_item_id == item_id, Reminder.status.in_(["paid", "marked_done"]))))


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, body: PlanUpdateIn, ctx: MemberCtx = Depends(require_member),
                db: Session = Depends(get_db)):
    plan = _get_plan(db, ctx, plan_id)
    if plan.version != body.version:
        raise ApiError(409, "version_conflict", "План уже изменил другой участник. Обновите и повторите правку.",
                       current=plan_out(db, plan))

    existing = {i.id: i for i in plan.items}
    incoming_ids = {i.id for i in body.items if i.id}
    if unknown := incoming_ids - existing.keys():
        raise ApiError(400, "unknown_item", f"Статьи {sorted(unknown)} не принадлежат плану")

    money_changed = False
    any_changed = False
    # удалённые статьи
    for item_id, item in list(existing.items()):
        if item_id not in incoming_ids:
            if _item_is_paid(db, item_id):
                raise ApiError(409, "item_paid", f"Статья «{item.title}» уже оплачена — её нельзя удалить")
            db.execute(update(Reminder).where(Reminder.budget_item_id == item_id, Reminder.status == "open")
                       .values(status="cancelled", closed_at=utcnow()))
            db.execute(update(Reminder).where(Reminder.budget_item_id == item_id).values(budget_item_id=None))
            db.execute(update(Payment).where(Payment.budget_item_id == item_id).values(budget_item_id=None))
            plan.items.remove(item)
            money_changed = any_changed = True
    # изменённые и новые
    for data in body.items:
        fields = data.model_dump(exclude={"id"})
        if data.id:
            item = existing[data.id]
            if item.amount != data.amount:
                if _item_is_paid(db, item.id):
                    raise ApiError(409, "item_paid", f"Статья «{item.title}» уже оплачена — сумму менять нельзя")
                money_changed = True
            for k, v in fields.items():
                if getattr(item, k) != v:
                    setattr(item, k, v)
                    any_changed = True
        else:
            plan.items.append(BudgetItem(**fields))
            money_changed = any_changed = True

    if not any_changed:
        # Ничего не поменялось — версию и статус не трогаем, повторное согласование не требуется.
        db.rollback()
        return {**plan_out(db, plan), "changed": False}

    new_status = plan.status
    if money_changed and plan.status in ("approved", "pending_approval"):
        new_status = "draft"  # суммы изменились — нужно согласовать заново
    db.flush()
    _bump_version(db, plan, body.version, status=new_status, updated_by=ctx.user.id,
                  **({"approved_by": None, "approved_at": None} if new_status == "draft" else {}))
    db.refresh(plan)
    if plan.status == "approved":
        _sync_reminders(db, plan)  # поменялись только названия/даты — согласование сохраняется
    log_event(db, "plan_updated", family_id=ctx.family.id, user_id=ctx.user.id, plan_id=plan.id,
              money_changed=money_changed, reapproval_required=money_changed and new_status == "draft")
    db.commit()
    db.refresh(plan)
    return {**plan_out(db, plan), "changed": True}


@router.post("/plans/{plan_id}/submit")
def submit_plan(plan_id: int, body: VersionIn, ctx: MemberCtx = Depends(require_member),
                db: Session = Depends(get_db)):
    plan = _get_plan(db, ctx, plan_id)
    if plan.status not in ("draft", "needs_revision"):
        raise ApiError(409, "bad_status", f"Нельзя отправить план в статусе «{plan.status}»")
    if not plan.items:
        raise ApiError(400, "empty_plan", "В плане нет статей")
    _bump_version(db, plan, body.version, status="pending_approval", submitted_by=ctx.user.id,
                  review_comment=None)
    log_event(db, "plan_submitted", family_id=ctx.family.id, user_id=ctx.user.id, plan_id=plan.id)
    db.commit()
    db.refresh(plan)
    return plan_out(db, plan)


def _adults_count(db: Session, family_id: int) -> int:
    return db.scalar(select(func.count()).select_from(FamilyMember).where(
        FamilyMember.family_id == family_id, FamilyMember.status == "active"))


def _check_reviewer(db: Session, ctx: MemberCtx, plan: BudgetPlan):
    if plan.status != "pending_approval":
        raise ApiError(409, "bad_status", f"План в статусе «{plan.status}», согласовывать нечего")
    # Согласует второй взрослый. Если взрослый в семье один — может сам.
    if plan.submitted_by == ctx.user.id and _adults_count(db, ctx.family.id) > 1:
        raise ApiError(403, "self_approve", "Согласовать должен второй взрослый")


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: int, body: VersionIn, ctx: MemberCtx = Depends(require_member),
                 db: Session = Depends(get_db)):
    plan = _get_plan(db, ctx, plan_id)
    _check_reviewer(db, ctx, plan)
    _bump_version(db, plan, body.version, status="approved", approved_by=ctx.user.id, approved_at=utcnow(),
                  review_comment=body.comment)
    db.refresh(plan)
    _sync_reminders(db, plan)
    log_event(db, "plan_approved", family_id=ctx.family.id, user_id=ctx.user.id, plan_id=plan.id,
              total=sum(i.amount for i in plan.items))
    db.commit()
    return plan_out(db, plan)


@router.post("/plans/{plan_id}/return")
def return_plan(plan_id: int, body: VersionIn, ctx: MemberCtx = Depends(require_member),
                db: Session = Depends(get_db)):
    plan = _get_plan(db, ctx, plan_id)
    _check_reviewer(db, ctx, plan)
    _bump_version(db, plan, body.version, status="needs_revision", review_comment=body.comment)
    log_event(db, "plan_returned", family_id=ctx.family.id, user_id=ctx.user.id, plan_id=plan.id,
              comment=body.comment)
    db.commit()
    db.refresh(plan)
    return plan_out(db, plan)


def _sync_reminders(db: Session, plan: BudgetPlan):
    """Для каждой статьи согласованного плана — одно напоминание. Оплаченные не трогаем."""
    for item in plan.items:
        r = db.scalar(select(Reminder).where(Reminder.budget_item_id == item.id).order_by(Reminder.id.desc()))
        if r is None or r.status == "cancelled":
            db.add(Reminder(family_id=plan.family_id, child_id=plan.child_id, budget_item_id=item.id,
                            title=item.title, amount=item.amount, due_date=item.due_date,
                            payee_url=item.payee_url, created_by=plan.approved_by))
        elif r.status == "open":
            r.title, r.amount, r.due_date, r.payee_url = item.title, item.amount, item.due_date, item.payee_url


# ---------------- напоминания ----------------

class ReminderIn(BaseModel):
    child_id: int
    title: str = Field(min_length=1, max_length=200)
    amount: int = Field(gt=0)
    due_date: date | None = None
    payee_url: str | None = None


def _get_reminder(db: Session, ctx: MemberCtx, rid: int) -> Reminder:
    r = db.get(Reminder, rid)
    if not r or r.family_id != ctx.family.id:
        raise ApiError(404, "reminder_not_found", "Напоминание не найдено")
    return r


@router.get("/reminders")
def list_reminders(status: str | None = None, ctx: MemberCtx = Depends(require_member),
                   db: Session = Depends(get_db)):
    q = select(Reminder).where(Reminder.family_id == ctx.family.id)
    if status:
        q = q.where(Reminder.status == status)
    return [reminder_out(db, r) for r in db.scalars(q.order_by(Reminder.due_date, Reminder.id))]


@router.post("/reminders")
def create_reminder(body: ReminderIn, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    get_family_child(db, ctx, body.child_id)
    r = Reminder(family_id=ctx.family.id, created_by=ctx.user.id, **body.model_dump())
    db.add(r)
    db.flush()
    log_event(db, "reminder_created", family_id=ctx.family.id, user_id=ctx.user.id, reminder_id=r.id)
    db.commit()
    return reminder_out(db, r)


@router.post("/reminders/{rid}/open-external")
def open_external(rid: int, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    """Переход на сайт секции. Только фиксируем событие — остатки и статус не меняются."""
    r = _get_reminder(db, ctx, rid)
    log_event(db, "reminder_external_opened", family_id=ctx.family.id, user_id=ctx.user.id, reminder_id=r.id)
    db.commit()
    return {"url": r.payee_url, "reminder": reminder_out(db, r)}


@router.post("/reminders/{rid}/mark-done")
def mark_done(rid: int, ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    """Ручная отметка «оплачено вне приложения». Деньги не двигаются."""
    r = _get_reminder(db, ctx, rid)
    res = db.execute(update(Reminder).where(Reminder.id == rid, Reminder.status == "open")
                     .values(status="marked_done", closed_at=utcnow(), closed_by=ctx.user.id))
    if res.rowcount == 0:
        db.rollback()
        raise ApiError(409, "reminder_closed", f"Напоминание уже в статусе «{r.status}»")
    log_event(db, "reminder_closed", family_id=ctx.family.id, user_id=ctx.user.id, reminder_id=r.id, via="manual")
    db.commit()
    db.refresh(r)
    return reminder_out(db, r)
