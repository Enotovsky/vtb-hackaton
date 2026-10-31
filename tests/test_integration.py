"""Проверки контракта нового дизайна поверх реальной SQLite и доменных функций."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.demo import ensure_demo
from app.db import SessionLocal
from app.main import app
from app.models import Account, Child, Payment, Reminder, User
from tests.conftest import register

BASE="/api/ui"


class Device:
    def __init__(self,token):
        self.client=TestClient(app)
        self.token=token

    def req(self,method,path,body=None):
        return self.client.request(method,BASE+path,json=body,headers={"Authorization":"Bearer "+self.token})

    def data(self,method,path,body=None):
        r=self.req(method,path,body)
        assert r.status_code==200,(r.status_code,r.text)
        return r.json()

    def snap(self):return self.data("GET","/snapshot")

    def pay(self,**values):
        body=dict(kind="expense",sourceId="family",amount=10000,title="Продукты",category="food",idempotencyKey=uuid4().hex)
        body.update(values)
        return self.data("POST","/payments",body)["operation"]


@pytest.fixture
def devices(monkeypatch):
    monkeypatch.setenv("VTB_DEMO_MODE","1")
    ensure_demo()
    c=TestClient(app)
    def login(name):
        r=c.post(BASE+"/demo/login",json={"userId":name})
        assert r.status_code==200,r.text
        return Device(r.json()["token"])
    return login("alex"),login("maria"),login("max")


def join(a,m):
    inv=a.data("POST","/invitations",{"recipientId":"maria"})["invitation"]
    m.data("POST",f"/invitations/{inv['id']}/respond",{"decision":"accept","consent":True})
    return inv


def plan_body():
    month=date.today().strftime("%Y-%m")
    return {"childId":"max","month":month,"expectedVersion":0,
            "items":[{"category":cat,"amount":amount,"due":month+"-25"} for cat,amount in
                     [("sections",600000),("clubs",0),("allowance",200000)]]}


def standalone_reminder(a):
    return a.data("POST","/reminders",{"title":"Бассейн для Макса","childId":"max","category":"sections",
                   "amount":600000,"due":date.today().isoformat()})["reminder"]


def test_designer_static_assets_and_api_docs():
    c=TestClient(app)
    for path in ["/","/mvp/app.js","/mvp/api.js","/mvp/budget.js","/styles.css","/presentation.html","/docs","/health"]:
        assert c.get(path).status_code==200,path
    assert "Общие планы" not in c.get("/").text  # экран входа отрисовывает JS
    assert "mvp/app.js" in c.get("/").text
    assert c.get("/data/family_bank.db").status_code==404
    assert c.get("/app/main.py").status_code==404


def test_demo_initialization_is_not_destructive(devices):
    a,_,_=devices
    a.pay(amount=300)
    ensure_demo()
    assert a.snap()["accounts"][0]["balance"]==799700


def test_separate_sessions_and_private_snapshots(devices):
    a,m,k=devices
    assert a.token!=m.token!=k.token
    s=m.snap()
    assert s["family"] is None and s["accounts"]==[] and s["children"]=={} and s["notes"]==[]
    join(a,m)
    s=m.snap()
    assert s["personalAccount"]["balance"]==1420000
    assert all("personalAccount" not in member and "balance" not in member for member in s["members"])
    ch=k.snap()
    assert ch["accounts"]==[] and ch["plans"]==[] and ch["notes"]==[]
    assert set(ch["children"])=={"max"} and len(ch["members"])==1
    assert "personalAccount" not in ch


def test_invite_consent_and_revocation(devices):
    a,m,_=devices
    inv=a.data("POST","/invitations",{"recipientId":"maria"})["invitation"]
    assert m.req("POST",f"/invitations/{inv['id']}/respond",{"decision":"accept"}).status_code==400
    assert a.req("POST",f"/invitations/{inv['id']}/respond",{"decision":"accept","consent":True}).status_code==404
    m.data("POST",f"/invitations/{inv['id']}/respond",{"decision":"accept","consent":True})
    a.data("DELETE","/members/maria")
    assert m.snap()["family"] is None
    assert m.req("POST","/notes",{"title":"secret"}).status_code==403
    assert m.req("POST",f"/invitations/{inv['id']}/respond",{"decision":"accept","consent":True}).status_code==409


def test_expired_invite_cannot_be_accepted(devices):
    a,m,_=devices
    inv=a.data("POST","/invitations",{"recipientId":"maria"})["invitation"]
    a.data("POST",f"/invitations/{inv['id']}/expire",{})
    assert m.req("POST",f"/invitations/{inv['id']}/respond",{"decision":"accept","consent":True}).status_code==409


def test_plan_version_zero_values_and_second_adult_approval(devices):
    a,m,_=devices;join(a,m)
    body=plan_body();p=a.data("PUT","/plans",body)["plan"]
    assert len(p["items"])==2 and p["status"]=="draft"
    assert a.snap()["accounts"][0]["balance"]==800000
    p=a.data("POST",f"/plans/{p['id']}/submit",{"version":p["version"]})["plan"]
    assert a.req("POST",f"/plans/{p['id']}/approve",{"version":p["version"]}).status_code==403
    p=m.data("POST",f"/plans/{p['id']}/approve",{"version":p["version"]})["plan"]
    assert p["status"]=="approved" and p["approvedBy"]==["alex","maria"]
    body["expectedVersion"]=p["version"];body["items"][1]["amount"]=50000
    assert a.req("PUT","/plans",body).status_code==200
    r=m.req("PUT","/plans",body)
    assert r.status_code==409 and r.json()["error"]["code"]=="CONFLICT"


def test_cannot_approve_own_plan_even_without_second_member(devices):
    a,_,_=devices;p=a.data("PUT","/plans",plan_body())["plan"]
    p=a.data("POST",f"/plans/{p['id']}/submit",{"version":p["version"]})["plan"]
    assert a.req("POST",f"/plans/{p['id']}/approve",{"version":p["version"]}).status_code==403


def test_pocket_money_reminder_is_a_transfer_not_an_expense(devices):
    a,m,k=devices;join(a,m)
    p=a.data("PUT","/plans",plan_body())["plan"]
    p=a.data("POST",f"/plans/{p['id']}/submit",{"version":p["version"]})["plan"]
    m.data("POST",f"/plans/{p['id']}/approve",{"version":p["version"]})
    r=next(x for x in a.snap()["reminders"] if x["category"]=="allowance")
    assert a.req("POST","/payments",dict(kind="expense",sourceId="family",childId="max",amount=r["amount"],reminderId=r["id"],idempotencyKey=uuid4().hex)).status_code==400
    op=a.pay(kind="allowance",childId="max",amount=r["amount"],reminderId=r["id"])
    assert op["status"]=="completed" and op["kind"]=="allowance"
    s=a.snap()
    assert s["accounts"][0]["balance"]==600000 and k.snap()["children"]["max"]["balance"]==200000
    assert next(x for x in s["reminders"] if x["id"]==r["id"])["status"]=="paid"
    assert sum(x["amount"] for x in s["operations"] if x["kind"] in ("expense","child_purchase"))==0


def test_pending_domain_payment_can_be_found_after_envelope_link_is_lost(devices):
    from app.ui_models import UiOperation
    a,_,_=devices;key=uuid4().hex
    original=a.pay(idempotencyKey=key)
    with SessionLocal() as db:
        e=db.scalar(select(UiOperation).where(UiOperation.key==key));e.payment_id=None;db.commit()
    restored=a.data("GET","/operations/lookup?key="+key)["operation"]
    assert restored["id"]==original["id"] and restored["status"]=="completed"
    assert a.pay(idempotencyKey=key)["id"]==original["id"]
    assert a.snap()["accounts"][0]["balance"]==790000


def test_monthly_budget_conflict_has_no_money_effect(devices):
    a,m,_=devices;join(a,m)
    b=a.snap()["monthlyBudgets"][0]
    payload={"month":b["month"],"expectedVersion":b["version"],"limits":{**b["limits"],"food":2000000}}
    a.data("PUT","/monthly-budget",payload)
    r=m.req("PUT","/monthly-budget",payload)
    assert r.status_code==409 and r.json()["error"]["code"]=="BUDGET_CONFLICT"
    assert a.snap()["accounts"][0]["balance"]==800000


def test_payment_allowance_purchase_consolidation_and_replay(devices):
    a,m,k=devices;join(a,m)
    reminder=standalone_reminder(a)
    key=uuid4().hex
    op=a.pay(amount=600000,title=reminder["title"],category="sections",childId="max",reminderId=reminder["id"],idempotencyKey=key)
    replay=a.pay(amount=600000,title=reminder["title"],category="sections",childId="max",reminderId=reminder["id"],idempotencyKey=key)
    assert op["id"]==replay["id"] and op["status"]=="completed"
    a.pay(kind="allowance",childId="max",amount=200000,title="Карманные")
    k.pay(kind="child_purchase",sourceId="child:max",childId="max",amount=30000,title="Обед")
    s=a.snap()
    assert s["accounts"][0]["balance"]==0 and s["children"]["max"]["balance"]==170000
    assert s["children"]["max"]["spentThisMonth"]==30000
    expenses=[o for o in s["operations"] if o["kind"] in ("expense","child_purchase") and o["status"]=="completed"]
    assert sum(o["amount"] for o in expenses)==630000
    assert len(s["operations"])==3
    assert m.snap()["operations"]==s["operations"]


@pytest.mark.parametrize("change",[{"title":"Другое"},{"category":"fun"},{"childId":"sonya"},{"simulate":"decline"},{"amount":999}])
def test_entire_payment_payload_is_idempotent(devices,change):
    a,_,_=devices;key=uuid4().hex
    a.pay(idempotencyKey=key)
    body=dict(kind="expense",sourceId="family",amount=10000,title="Продукты",category="food",idempotencyKey=key)
    body.update(change)
    assert a.req("POST","/payments",body).status_code==409


def test_parallel_payment_once(devices):
    a,_,_=devices;key=uuid4().hex
    with ThreadPoolExecutor(max_workers=6) as pool:
        ops=list(pool.map(lambda _:a.pay(amount=1200,idempotencyKey=key),range(6)))
    assert all(o["status"] in ("pending","completed") for o in ops)
    assert len({o["id"] for o in ops})==1
    assert a.snap()["accounts"][0]["balance"]==798800
    assert len(a.snap()["operations"])==1


def test_two_adults_cannot_pay_one_reminder_twice(devices):
    a,m,_=devices;join(a,m);r=standalone_reminder(a)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ops=list(pool.map(lambda device:device.pay(amount=r["amount"],reminderId=r["id"],childId="max"),[a,m]))
    assert sorted(o["status"] for o in ops)==["completed","failed"]
    assert a.snap()["accounts"][0]["balance"]==200000


def test_failed_payments_do_not_change_balances(devices):
    a,_,_=devices
    assert a.pay(amount=800001)["status"]=="failed"
    assert a.pay(amount=1000,simulate="decline")["status"]=="failed"
    assert a.snap()["accounts"][0]["balance"]==800000


def test_child_limits_freeze_and_no_privilege_escalation(devices):
    a,_,k=devices
    a.pay(kind="allowance",childId="max",amount=200000)
    assert k.pay(kind="child_purchase",sourceId="child:max",childId="max",amount=100001)["status"]=="failed"
    a.data("PUT","/children/max",{"limit":40000,"frozen":False})
    assert k.pay(kind="child_purchase",sourceId="child:max",childId="max",amount=30000)["status"]=="completed"
    a.data("PUT","/children/max",{"limit":40000,"frozen":True})
    assert "заморожена" in k.pay(kind="child_purchase",sourceId="child:max",childId="max",amount=100)["error"]
    assert k.req("PUT","/children/max",{"limit":999999,"frozen":False}).status_code==403
    assert k.req("POST","/notes",{"title":"X"}).status_code==403
    assert k.req("POST","/payments",dict(kind="expense",sourceId="family",amount=1,idempotencyKey=uuid4().hex)).status_code==403


def test_reminder_manual_links_and_note_persistence(devices):
    a,m,_=devices;join(a,m)
    r=a.data("POST","/reminders",{"title":"Кружок","childId":"max","category":"clubs","amount":1000,
                                   "due":date.today().isoformat(),"url":"https://example.com/pay"})["reminder"]
    assert r["category"]=="clubs"
    a.data("POST",f"/reminders/{r['id']}/manual",{})
    assert a.snap()["accounts"][0]["balance"]==800000
    n=a.data("POST","/notes",{"title":"Встреча","text":"В субботу"})["note"]
    m.data("POST","/notes",{**n,"done":True,"authorId":"maria"})
    changed=next(x for x in a.snap()["notes"] if x["id"]==n["id"])
    assert changed["done"] and changed["authorId"]=="alex"
    a.data("DELETE","/notes/"+n["id"])
    assert not any(x["id"]==n["id"] for x in m.snap()["notes"])


def test_unsafe_link_rejected(devices):
    a,_,_=devices
    for url in ["javascript:alert(1)","http://example.com","https://user:pass@example.com"]:
        assert a.req("POST","/reminders",{"title":"x","childId":"max","amount":100,"due":date.today().isoformat(),"url":url}).status_code==422


def test_goal_and_personal_topup_are_real_transfers(devices):
    a,_,_=devices;before=a.snap()
    a.pay(kind="topup",amount=10000)
    after=a.snap()
    assert after["personalAccount"]["balance"]==before["personalAccount"]["balance"]-10000
    assert after["accounts"][0]["balance"]==810000
    a.pay(kind="goal",amount=5000)
    after=a.snap()
    assert after["accounts"][0]["balance"]==805000 and after["goal"]["saved"]==before["goal"]["saved"]+5000
    assert all(o["kind"] not in ("expense","child_purchase") for o in after["operations"])


def test_new_accounts_offers_reset_and_session_survival(devices):
    a,m,k=devices;join(a,m)
    assert m.req("POST","/accounts",{"name":"Отпуск"}).status_code==403
    acc=a.data("POST","/accounts",{"name":"Отпуск"})["account"]
    assert acc["balance"]==0
    a.data("POST","/offers/save",{"id":"weekend"})
    assert "weekend" in m.snap()["savedOffers"]
    a.pay(amount=123)
    a.data("POST","/demo/reset",{})
    assert a.snap()["accounts"][0]["balance"]==800000 and a.snap()["operations"]==[]
    assert m.snap()["family"] is None and k.snap()["children"]["max"]["balance"]==0


def test_demo_routes_can_be_disabled(devices,monkeypatch):
    a,_,_=devices;monkeypatch.setenv("VTB_DEMO_MODE","0")
    assert TestClient(app).post(BASE+"/demo/login",json={"userId":"alex"}).status_code==403
    assert a.req("POST","/demo/reset",{}).status_code==403


def test_foreign_family_cannot_read_or_mutate_objects(devices):
    a,_,_=devices
    outsider=register("stranger@test.ru")
    outsider.post("/api/family",{"name":"Другие"})
    other=Device(outsider.token)
    n=a.data("POST","/notes",{"title":"Личное для семьи"})["note"]
    assert other.req("DELETE","/notes/"+n["id"]).status_code==404
    assert other.req("PUT","/children/max",{"limit":1,"frozen":True}).status_code==404


@pytest.mark.parametrize("amount",[0,-1,1.5,True,"100"])
def test_money_requires_positive_integer_kopecks(devices,amount):
    a,_,_=devices
    r=a.req("POST","/payments",dict(kind="expense",sourceId="family",amount=amount,idempotencyKey=uuid4().hex))
    assert r.status_code==422
