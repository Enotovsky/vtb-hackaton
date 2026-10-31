"""Идемпотентная подготовка учебной семьи. Повторный запуск не стирает историю."""
from datetime import date

from sqlalchemy import select

from app.core import hash_password, log_event
from app.db import SessionLocal, init_db
from app.models import Account, Card, Child, Family, FamilyMember, Reminder, User
from app.routers.auth import random_pan
from app.ui_models import Goal, MonthlyBudget, Note


def ensure_demo():
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email=="alex@test.ru")):
            return
        alex=User(email="alex@test.ru",name="Александр",password_hash=hash_password("demo2026"))
        maria=User(email="maria@test.ru",name="Мария",password_hash=hash_password("demo2026"))
        db.add_all([alex,maria]);db.flush()
        fam=Family(name="Ивановы",owner_id=alex.id);db.add(fam);db.flush()
        db.add(FamilyMember(family_id=fam.id,user_id=alex.id,role="owner"))
        for user,balance in [(alex,8_460_000),(maria,1_420_000)]:
            a=Account(kind="personal",name="Личный счёт",owner_user_id=user.id,balance=balance)
            db.add(a);db.flush();db.add(Card(account_id=a.id,holder_user_id=user.id,pan_masked=random_pan()))
        shared=Account(kind="shared",name="Общий счёт",family_id=fam.id,balance=800_000)
        db.add(shared);db.flush();db.add(Card(account_id=shared.id,holder_user_id=alex.id,pan_masked="2200 •••• •••• 2048"))
        goal_account=Account(kind="shared",name="Накопительная цель",family_id=fam.id,balance=6_850_000)
        db.add(goal_account);db.flush();db.add(Goal(family_id=fam.id,account_id=goal_account.id))
        max_=Child(family_id=fam.id,name="Макс",birth_date=date(2015,4,12),balance=0,monthly_limit=100_000,access_code="MAX2026")
        sonya=Child(family_id=fam.id,name="Соня",birth_date=date(2018,9,1),balance=180_000,monthly_limit=300_000,access_code="SONYA2026")
        db.add_all([max_,sonya]);db.flush()
        today=date.today();month=today.strftime("%Y-%m")
        db.add(MonthlyBudget(family_id=fam.id,month=month,limits={"food":3_500_000,"home":3_000_000,"kids":2_000_000,"fun":1_500_000}))
        db.add(Note(family_id=fam.id,author_id=alex.id,title="Планы на выходные",text="Обсудить семейный поход в кино и выбрать сеанс."))
        log_event(db,"family_created",family_id=fam.id,user_id=alex.id,source="demo_seed")
        db.commit()
