"""Доменные модели. Все суммы хранятся в копейках (int), чтобы не было ошибок округления."""
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthSession(Base):
    """Сессия устройства. Либо взрослый (user_id), либо ребёнок (child_id)."""

    __tablename__ = "auth_sessions"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"))
    device: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Family(Base):
    __tablename__ = "families"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FamilyMember(Base):
    """Один пользователь — одна строка на семью (уникальный ключ), повторное принятие не создаёт дубль."""

    __tablename__ = "family_members"
    __table_args__ = (UniqueConstraint("family_id", "user_id", name="uq_member_family_user"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # owner | adult
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | removed | left
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    inviter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    invitee_email: Mapped[str] = mapped_column(String(255), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # sent | accepted | declined | expired | cancelled
    status: Mapped[str] = mapped_column(String(20), default="sent")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime)
    accepted_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Account(Base):
    """kind=personal — личный счёт (owner_user_id), kind=shared — общий счёт семьи (family_id)."""

    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[int | None] = mapped_column(ForeignKey("families.id"), index=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Card(Base):
    """Привязка карты к счёту. Держатель карты — конкретный взрослый."""

    __tablename__ = "cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    holder_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    pan_masked: Mapped[str] = mapped_column(String(25))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | blocked
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Child(Base):
    """Профиль ребёнка и его виртуальный кошелёк (отдельный баланс, лимит, заморозка)."""

    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    birth_date: Mapped[date | None] = mapped_column(Date)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    monthly_limit: Mapped[int] = mapped_column(Integer, default=0)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    access_code: Mapped[str | None] = mapped_column(String(20), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BudgetPlan(Base):
    """Бюджет ребёнка на месяц. version — оптимистичная блокировка против затирания правок."""

    __tablename__ = "budget_plans"
    __table_args__ = (UniqueConstraint("child_id", "month", name="uq_plan_child_month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    # draft | pending_approval | approved | needs_revision
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    submitted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list["BudgetItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="BudgetItem.id"
    )


class BudgetItem(Base):
    __tablename__ = "budget_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("budget_plans.id"), index=True)
    category: Mapped[str] = mapped_column(String(20))  # section | club | pocket_money | other
    title: Mapped[str] = mapped_column(String(200))
    amount: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    payee_url: Mapped[str | None] = mapped_column(String(500))

    plan: Mapped[BudgetPlan] = relationship(back_populates="items")


class Payment(Base):
    """Тестовый платёж / перевод. Idempotency-key защищает от повторного списания."""

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("payer_user_id", "idempotency_key", name="uq_payment_idem"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))  # purchase | child_topup | shared_topup
    family_id: Mapped[int | None] = mapped_column(ForeignKey("families.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    target_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    payer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"))
    budget_item_id: Mapped[int | None] = mapped_column(ForeignKey("budget_items.id"))
    reminder_id: Mapped[int | None] = mapped_column(ForeignKey("reminders.id"))
    amount: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | completed | failed
    failure_reason: Mapped[str | None] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Operation(Base):
    """Журнал движения денег (строка = изменение баланса одного счёта или кошелька ребёнка)."""

    __tablename__ = "operations"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int | None] = mapped_column(ForeignKey("families.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    child_wallet_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"), index=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id"))
    amount: Mapped[int] = mapped_column(Integer)  # со знаком: <0 списание, >0 зачисление
    balance_after: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(30))
    payer_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    payer_child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"))
    child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"))  # к кому относится расход
    purpose: Mapped[str] = mapped_column(String(300))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChildPurchase(Base):
    """Тестовая покупка ребёнка из его виртуального кошелька."""

    __tablename__ = "child_purchases"
    __table_args__ = (UniqueConstraint("child_id", "idempotency_key", name="uq_child_purchase_idem"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(Integer)
    merchant: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    failure_reason: Mapped[str | None] = mapped_column(String(100))
    month: Mapped[str] = mapped_column(String(7), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"))
    budget_item_id: Mapped[int | None] = mapped_column(ForeignKey("budget_items.id"))
    title: Mapped[str] = mapped_column(String(200))
    amount: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    payee_url: Mapped[str | None] = mapped_column(String(500))
    # open | paid | marked_done | cancelled
    status: Mapped[str] = mapped_column(String(20), default="open")
    payment_id: Mapped[int | None] = mapped_column(Integer)  # payments.id (без FK — избегаем цикла)
    operation_id: Mapped[int | None] = mapped_column(Integer)  # operations.id
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Event(Base):
    """Продуктовые события для метрик (создание семьи, приглашения, согласования, оплаты...)."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int | None] = mapped_column(ForeignKey("families.id"), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"))
    type: Mapped[str] = mapped_column(String(50), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BugReport(Base):
    __tablename__ = "bug_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    reporter: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    steps: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    actual: Mapped[str] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
