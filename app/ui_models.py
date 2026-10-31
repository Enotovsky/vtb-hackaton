"""Данные экранов, отсутствовавшие в исходном API. Деньги остаются в Account/Child."""
from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MonthlyBudget(Base):
    __tablename__ = "ui_monthly_budgets"
    __table_args__ = (UniqueConstraint("family_id", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    month: Mapped[str] = mapped_column(String(7))
    version: Mapped[int] = mapped_column(default=1)
    limits: Mapped[dict] = mapped_column(JSON)


class Note(Base):
    __tablename__ = "ui_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text, default="")
    done: Mapped[bool] = mapped_column(default=False)


class Goal(Base):
    __tablename__ = "ui_goals"
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True)
    name: Mapped[str] = mapped_column(String(100), default="Лето у моря")
    target: Mapped[int] = mapped_column(default=12_000_000)


class SavedOffer(Base):
    __tablename__ = "ui_saved_offers"
    __table_args__ = (UniqueConstraint("family_id", "offer_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    offer_id: Mapped[str] = mapped_column(String(40))


class ReminderCategory(Base):
    __tablename__ = "ui_reminder_categories"
    reminder_id: Mapped[int] = mapped_column(ForeignKey("reminders.id"), primary_key=True)
    category: Mapped[str] = mapped_column(String(20))


class UiOperation(Base):
    """Конверт запроса для строгой идемпотентности и восстановления после потери ответа.

    Создаётся до исполнения. Банковское состояние читается из Payment/ChildPurchase.
    После падения между этапами показывается pending, а не выдуманный отказ.
    """
    __tablename__ = "ui_operations"
    __table_args__ = (UniqueConstraint("actor", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    actor: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"))
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("child_purchases.id"))
    error: Mapped[str | None] = mapped_column(String(300))
