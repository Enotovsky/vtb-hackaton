"""Тестовая семья для демо. Запуск: python seed.py  (база пересоздаётся!)

Все действия идут через API — поэтому журнал событий и проверки работают так же, как в продукте.
"""
import os
import uuid
from datetime import date

DB_FILE = "family_bank.db"

for suffix in ("", "-wal", "-shm"):
    if os.path.exists(DB_FILE + suffix):
        os.remove(DB_FILE + suffix)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def call(c, method, url, token=None, json=None, idem=None):
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if idem:
        h["Idempotency-Key"] = idem
    r = c.request(method, url, json=json, headers=h)
    assert r.status_code == 200, f"{method} {url}: {r.status_code} {r.text}"
    return r.json()


with TestClient(app) as c:
    mom = call(c, "POST", "/api/auth/register", json={"email": "mama@test.ru", "name": "Анна (мама)", "password": "1234"})["token"]
    dad = call(c, "POST", "/api/auth/register", json={"email": "papa@test.ru", "name": "Олег (папа)", "password": "1234"})["token"]

    call(c, "POST", "/api/family", mom, {"name": "Семья Петровых"})
    inv = call(c, "POST", "/api/family/invitations", mom, {"email": "papa@test.ru"})
    call(c, "POST", f"/api/invitations/{inv['token']}/accept", dad)

    child = call(c, "POST", "/api/family/children", mom,
                 {"name": "Маша", "birth_date": "2015-04-12", "monthly_limit": 1500_00})
    call(c, "POST", "/api/family/children", mom, {"name": "Ваня", "birth_date": "2018-09-01", "monthly_limit": 500_00})

    shared = next(a for a in call(c, "GET", "/api/accounts", mom) if a["kind"] == "shared")
    call(c, "POST", f"/api/children/{child['id']}/topup", dad,
         {"account_id": shared["id"], "amount": 1000_00}, idem=str(uuid.uuid4()))
    call(c, "POST", "/api/payments", dad, {"account_id": shared["id"], "amount": 3450_00, "purpose": "Продукты"},
         idem=str(uuid.uuid4()))

    month = date.today().strftime("%Y-%m")
    plan = call(c, "POST", f"/api/children/{child['id']}/plans", mom, {"month": month, "items": [
        {"category": "section", "title": "Плавание", "amount": 4500_00, "due_date": f"{month}-10",
         "payee_url": "https://example.com/swim"},
        {"category": "club", "title": "Рисование", "amount": 2500_00, "due_date": f"{month}-15"},
        {"category": "pocket_money", "title": "Карманные деньги", "amount": 1500_00, "due_date": f"{month}-01"},
    ]})
    call(c, "POST", f"/api/plans/{plan['id']}/submit", mom, {"version": plan["version"]})

print(f"""Готово.
  Мама (владелец): mama@test.ru / 1234
  Папа:            papa@test.ru / 1234
  Код входа Маши:  {child['access_code']}
  План Маши на {month} отправлен мамой — его должен согласовать папа (со второго устройства).
Запуск сервера:  uvicorn app.main:app --host 0.0.0.0 --port 8000""")
