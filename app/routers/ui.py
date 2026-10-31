"""Адаптер интерфейса дизайнера к доменным функциям FastAPI.

Ни одного JSON-хранилища Node.js: состояния, заметки, бюджеты и платежи в SQLite.
Исходный API /api/* сохранён. Новый интерфейс обращается к /api/ui/*.
"""
import os
import re
import time
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import (ApiError, MemberCtx, active_membership, current_user,
                      get_auth_session, get_family_child, log_event, require_member,
                      require_owner, request_hash)
from app.db import get_db
from app.models import (Account, AuthSession, BudgetItem, BudgetPlan, Card, Child,
                        ChildPurchase, Event, Family, FamilyMember, Invitation,
                        Operation, Payment, Reminder, User, utcnow)
from app.payments import PaymentRequest, accessible_account, execute_payment
from app.routers import auth, budget, children, family
from app.ui_models import Goal, MonthlyBudget, Note, ReminderCategory, SavedOffer, UiOperation

router = APIRouter(prefix="/api/ui", tags=["frontend integration"])
CATEGORIES = [dict(id=k, name=n, color=c, icon=i) for k,n,c,i in [
    ("sections","Секции","#a18bd8","sport"),("clubs","Кружки","#c0afe7","spark"),
    ("food","Продукты","#0671fa","food"),("home","Дом","#47b5ce","home"),
    ("fun","Досуг","#eeb078","gift")]]
TO_DOMAIN = {"sections":"section", "clubs":"club", "allowance":"pocket_money"}
TO_UI = {v:k for k,v in TO_DOMAIN.items()}
TITLES = {"sections":"Секции", "clubs":"Кружки", "allowance":"Карманные деньги"}
ERRORS = {"insufficient_funds":"Недостаточно средств на выбранном счёте.",
          "limit_exceeded":"Превышен месячный лимит детской карты.",
          "wallet_frozen":"Детская карта заморожена.",
          "provider_error":"Учебный банк отклонил операцию.",
          "reminder_closed":"Этот расход уже обработан.",
          "internal_error":"Не удалось выполнить учебную операцию."}
Money = int


def demo_enabled():
    if os.environ.get("VTB_DEMO_MODE", "0") != "1":
        raise ApiError(403, "DEMO_DISABLED", "Учебные входы отключены. Используйте /api/auth/login.")


def uid(db, value):
    u = db.get(User, value) if value else None
    return {"alex@test.ru":"alex", "maria@test.ru":"maria"}.get(u.email, f"u{value}") if u else ""


def cid(c):
    return {"Макс":"max", "Соня":"sonya"}.get(c.name, f"c{c.id}")


def find_child(db, external, family_id):
    for child in db.scalars(select(Child).where(Child.family_id == family_id)):
        if cid(child) == external:
            return child
    raise ApiError(404, "NOT_FOUND", "Ребёнок не найден.")


def number(value):
    if not str(value).isdigit():
        raise ApiError(404, "NOT_FOUND", "Объект не найден.")
    return int(value)


def account_id(db, external, ctx):
    if external == "family":
        aid = db.scalar(select(Account.id).where(Account.family_id == ctx.family.id,
                         Account.kind == "shared").order_by(Account.id))
        if aid:
            return aid
    if external.startswith("account:"):
        aid = number(external.split(":",1)[1])
        accessible_account(db, ctx, ctx.user.id, aid)
        return aid
    raise ApiError(404, "NOT_FOUND", "Общий счёт не найден.")


def public_account(db, a, family_id):
    first = db.scalar(select(Account.id).where(Account.family_id == family_id,
                      Account.kind == "shared").order_by(Account.id))
    return {"id":"family" if a.id == first else f"account:{a.id}",
            "name":a.name, "balance":a.balance}


def person(db, u, role):
    external = uid(db,u.id)
    return {"id":external,"name":u.name,"role":role,
            "tone":"me" if role=="owner" else "partner"}


def public_child(c):
    return {"id":cid(c),"name":c.name,"role":"child","tone":cid(c),"emoji":"👦🏻" if cid(c)=="max" else "👧🏻"}


def invitation_out(db, i):
    recipient = db.scalar(select(User).where(User.email == i.invitee_email))
    return {"id":str(i.id),"from":uid(db,i.inviter_id),
            "to":uid(db,recipient.id) if recipient else "", "senderName":db.get(User,i.inviter_id).name,
            "recipientName":recipient.name if recipient else i.invitee_email, "status":i.status,
            "createdAt":i.created_at.isoformat()+"Z", "expiresAt":i.expires_at.isoformat()+"Z"}


def plan_out(db, p):
    return {"id":str(p.id),"childId":cid(db.get(Child,p.child_id)),"month":p.month,
            "version":p.version,"status":{"pending_approval":"pending","needs_revision":"changes_requested"}.get(p.status,p.status),
            "authorId":uid(db,p.submitted_by or p.updated_by),
            "approvedBy":[uid(db,p.submitted_by),uid(db,p.approved_by)] if p.approved_by else [],
            "comment":p.review_comment or "", "updatedAt":p.updated_at.isoformat()+"Z",
            "items":[{"id":str(i.id),"category":TO_UI.get(i.category,"sections"),"amount":i.amount,
                      "due":(i.due_date or date.fromisoformat(p.month+"-25")).isoformat()} for i in p.items]}


def reminder_out(db, r):
    item = db.get(BudgetItem,r.budget_item_id) if r.budget_item_id else None
    category = db.get(ReminderCategory,r.id)
    envelope = db.scalar(select(UiOperation).where(UiOperation.payment_id==r.payment_id)) if r.payment_id else None
    return {"id":str(r.id),"childId":cid(db.get(Child,r.child_id)),"title":r.title,"amount":r.amount,
            "category":category.category if category else TO_UI.get(item.category,"sections") if item else "sections",
            "due":(r.due_date or date.today()).isoformat(), "url":r.payee_url or "",
            "status":"manual" if r.status=="marked_done" else r.status,
            "operationId":f"ui:{envelope.id}" if envelope else f"payment:{r.payment_id}" if r.payment_id else None}


def operation_out(db, envelope):
    data = dict(envelope.payload)
    data.pop("simulate",None)
    data.pop("idempotencyKey",None)
    p = db.get(Payment,envelope.payment_id) if envelope.payment_id else None
    cp = db.get(ChildPurchase,envelope.purchase_id) if envelope.purchase_id else None
    # Восстановление после фиксации доменного платежа до обновления конверта.
    domain_key = f"ui:{envelope.id}"
    if not p and not cp:
        if data["kind"] == "child_purchase":
            cp = db.scalar(select(ChildPurchase).where(ChildPurchase.idempotency_key==domain_key))
        else:
            p = db.scalar(select(Payment).where(Payment.idempotency_key==domain_key))
    obj = p or cp
    created = obj.created_at if obj else None
    # ID конверта стабилен и при pending, и после подтверждения доменной операции.
    data.update(id=f"ui:{envelope.id}",
                payerId=envelope.actor, status=obj.status if obj else "failed" if envelope.error else "pending",
                date=(created or utcnow()).date().isoformat(), createdAt=(created or utcnow()).isoformat()+"Z",
                error=ERRORS.get(obj.failure_reason,obj.failure_reason) if obj else envelope.error)
    if p and p.finished_at:
        data["completedAt"] = p.finished_at.isoformat()+"Z"
    return data


def visible_operations(db, fam_id, child=None):
    result = []
    represented_payments, represented_purchases = set(), set()
    for e in db.scalars(select(UiOperation).where(UiOperation.family_id==fam_id).order_by(UiOperation.id.desc())):
        if child and (e.payload.get("childId")!=cid(child) or e.payload.get("kind") not in ("allowance","child_purchase")):
            continue
        o = operation_out(db,e)
        result.append(o)
        pid=e.payment_id or db.scalar(select(Payment.id).where(Payment.idempotency_key==f"ui:{e.id}"))
        cpid=e.purchase_id or db.scalar(select(ChildPurchase.id).where(ChildPurchase.idempotency_key==f"ui:{e.id}"))
        if pid:represented_payments.add(pid)
        if cpid:represented_purchases.add(cpid)
    shared = list(db.scalars(select(Account.id).where(Account.family_id==fam_id)))
    query = select(Payment).where(Payment.family_id==fam_id,
                or_(Payment.account_id.in_(shared),Payment.target_account_id.in_(shared)))
    for p in db.scalars(query):
        if p.id in represented_payments:continue
        kind={"purchase":"expense","child_topup":"allowance","shared_topup":"topup"}[p.kind]
        if child and (p.child_id!=child.id or kind!="allowance"):continue
        item=db.get(BudgetItem,p.budget_item_id) if p.budget_item_id else None
        source=db.get(Account,p.target_account_id if kind=="topup" else p.account_id)
        result.append({"id":f"payment:{p.id}","kind":kind,"amount":p.amount,"title":p.purpose,
                       "sourceId":public_account(db,source,fam_id)["id"],"payerId":uid(db,p.payer_user_id),
                       "childId":cid(db.get(Child,p.child_id)) if p.child_id else "",
                       "category":TO_UI.get(item.category,"food") if item else "food",
                       "reminderId":str(p.reminder_id) if p.reminder_id else "", "status":p.status,
                       "date":p.created_at.date().isoformat(),"createdAt":p.created_at.isoformat()+"Z",
                       "error":ERRORS.get(p.failure_reason,p.failure_reason)})
    child_ids = [child.id] if child else list(db.scalars(select(Child.id).where(Child.family_id==fam_id)))
    for cp in db.scalars(select(ChildPurchase).where(ChildPurchase.child_id.in_(child_ids))):
        if cp.id in represented_purchases:continue
        ch=db.get(Child,cp.child_id)
        result.append({"id":f"purchase:{cp.id}","kind":"child_purchase","amount":cp.amount,
                       "title":cp.merchant,"sourceId":"child:"+cid(ch),"payerId":cid(ch),"childId":cid(ch),
                       "category":"food","reminderId":"","status":cp.status,
                       "date":cp.created_at.date().isoformat(),"createdAt":cp.created_at.isoformat()+"Z",
                       "error":ERRORS.get(cp.failure_reason,cp.failure_reason)})
    return sorted(result,key=lambda o:o["createdAt"],reverse=True)


def snapshot_data(db, sess):
    family.expire_due_invitations(db)
    today=date.today()
    result={"revision":today.toordinal()*1_000_000_000+(db.scalar(select(func.max(Event.id))) or 0),
            "today":today.isoformat(), "categories":CATEGORIES,"invitations":[],"members":[],
            "children":{},"accounts":[],"operations":[],"plans":[],"monthlyBudgets":[],
            "notes":[],"reminders":[],"savedOffers":[],"goal":None,"family":None}
    if sess.child_id:
        child=db.get(Child,sess.child_id)
        fam=db.get(Family,child.family_id)
        result.update(user=public_child(child), family={"id":str(fam.id),"name":fam.name}, members=[public_child(child)],
                      operations=visible_operations(db,fam.id,child))
        kids=[child]
    else:
        user=db.get(User,sess.user_id)
        m=active_membership(db,user.id)
        result["user"]=person(db,user,m.role if m else "adult")
        own=db.scalar(select(Account).where(Account.owner_user_id==user.id,Account.kind=="personal").order_by(Account.id))
        result["personalAccount"]={"id":"personal:"+uid(db,user.id),"name":"Мой личный счёт","ownerId":uid(db,user.id),"balance":own.balance if own else 0}
        iq=select(Invitation).where(Invitation.invitee_email==user.email)
        if m:iq=select(Invitation).where(or_(Invitation.invitee_email==user.email,Invitation.family_id==m.family_id))
        result["invitations"]=[invitation_out(db,i) for i in db.scalars(iq)]
        if not m:return result
        fam=db.get(Family,m.family_id)
        members=db.scalars(select(FamilyMember).where(FamilyMember.family_id==fam.id,FamilyMember.status=="active")).all()
        kids=db.scalars(select(Child).where(Child.family_id==fam.id)).all()
        result["members"]=[person(db,db.get(User,x.user_id),x.role) for x in members]+[public_child(k) for k in kids]
        result["family"]={"id":str(fam.id),"name":fam.name,"ownerId":uid(db,fam.owner_id),"members":[x["id"] for x in result["members"]]}
        goal=db.get(Goal,fam.id)
        result["accounts"]=[public_account(db,a,fam.id) for a in db.scalars(select(Account).where(Account.family_id==fam.id,Account.kind=="shared").order_by(Account.id)) if not goal or a.id!=goal.account_id]
        result["goal"]={"name":goal.name,"saved":db.get(Account,goal.account_id).balance,"target":goal.target} if goal else {"name":"Лето у моря","saved":0,"target":12_000_000}
        result["operations"]=visible_operations(db,fam.id)
        result["plans"]=[plan_out(db,p) for p in db.scalars(select(BudgetPlan).where(BudgetPlan.family_id==fam.id))]
        result["monthlyBudgets"]=[{"month":b.month,"version":b.version,"limits":b.limits} for b in db.scalars(select(MonthlyBudget).where(MonthlyBudget.family_id==fam.id))]
        result["notes"]=[{"id":str(n.id),"title":n.title,"text":n.text,"authorId":uid(db,n.author_id),"done":n.done} for n in db.scalars(select(Note).where(Note.family_id==fam.id).order_by(Note.id.desc()))]
        result["reminders"]=[reminder_out(db,r) for r in db.scalars(select(Reminder).where(Reminder.family_id==fam.id,Reminder.status!="cancelled"))]
        result["savedOffers"]=list(db.scalars(select(SavedOffer.offer_id).where(SavedOffer.family_id==fam.id)))
    for ch in kids:
        spent=family.spent_this_month(db,ch.id)
        result["children"][cid(ch)]={"balance":ch.balance,"limit":ch.monthly_limit,"frozen":ch.frozen,
                                    "spentThisMonth":spent,"remainingLimit":max(0,ch.monthly_limit-spent)}
    return result


@router.get("/snapshot")
def snapshot(sess:AuthSession=Depends(get_auth_session),db:Session=Depends(get_db)):
    return snapshot_data(db,sess)


class DemoLogin(BaseModel):
    userId: Literal["alex","maria","max"]


@router.post("/demo/login",dependencies=[Depends(demo_enabled)])
def demo_login(body:DemoLogin,db:Session=Depends(get_db)):
    if body.userId=="max":
        child=db.scalar(select(Child).where(Child.access_code=="MAX2026"))
        if not child:raise ApiError(409,"NOT_INITIALIZED","Выполните python run.py для подготовки учебной семьи.")
        token=auth._open_session(db,child_id=child.id,device="design demo")
    else:
        user=db.scalar(select(User).where(User.email==body.userId+"@test.ru"))
        if not user:raise ApiError(409,"NOT_INITIALIZED","Учебные данные не созданы. Запустите python run.py.")
        token=auth._open_session(db,user_id=user.id,device="design demo")
    db.commit()
    return {"token":token,"snapshot":snapshot_data(db,db.get(AuthSession,token))}


@router.post("/demo/logout")
def logout(sess:AuthSession=Depends(get_auth_session),db:Session=Depends(get_db)):
    return auth.logout(sess,db)


class InviteIn(BaseModel):
    recipientId: str


@router.post("/invitations")
def invite(body:InviteIn,ctx:MemberCtx=Depends(require_owner),db:Session=Depends(get_db)):
    if body.recipientId!="maria":raise ApiError(400,"VALIDATION","В учебном сценарии доступно приглашение Марии.")
    result=family.create_invitation(family.InviteIn(email="maria@test.ru"),ctx,db)
    return {"invitation":invitation_out(db,db.get(Invitation,result["id"]))}


class RespondIn(BaseModel):
    decision:Literal["accept","decline"]
    consent:bool=False


@router.post("/invitations/{inv_id}/respond")
def respond(inv_id:str,body:RespondIn,user:User=Depends(current_user),db:Session=Depends(get_db)):
    inv=db.get(Invitation,number(inv_id))
    if not inv or inv.invitee_email!=user.email:raise ApiError(404,"NOT_FOUND","Приглашение не найдено.")
    if body.decision=="accept":
        if not body.consent:raise ApiError(400,"CONSENT_REQUIRED","Подтвердите согласие на совместный доступ.")
        family.accept_invitation(inv.token,user,db)
        log_event(db,"shared_access_consent",family_id=inv.family_id,user_id=user.id,invitation_id=inv.id,terms_version="mvp-1")
        db.commit()
    else:family.decline_invitation(inv.token,user,db)
    return {"ok":True}


@router.post("/invitations/{inv_id}/cancel")
def cancel(inv_id:str,ctx:MemberCtx=Depends(require_owner),db:Session=Depends(get_db)):
    family.cancel_invitation(number(inv_id),ctx,db)
    return {"ok":True}


@router.post("/invitations/{inv_id}/expire",dependencies=[Depends(demo_enabled)])
def expire(inv_id:str,ctx:MemberCtx=Depends(require_owner),db:Session=Depends(get_db)):
    inv=db.get(Invitation,number(inv_id))
    if not inv or inv.family_id!=ctx.family.id:raise ApiError(404,"NOT_FOUND","Приглашение не найдено.")
    if inv.status!="sent":raise ApiError(409,"CONFLICT","Приглашение уже обработано.")
    inv.expires_at=utcnow();db.commit();family.expire_due_invitations(db)
    return {"ok":True}


@router.delete("/members/{external}")
def remove(external:str,ctx:MemberCtx=Depends(require_owner),db:Session=Depends(get_db)):
    for m in db.scalars(select(FamilyMember).where(FamilyMember.family_id==ctx.family.id)):
        if uid(db,m.user_id)==external:return family.remove_member(m.user_id,ctx,db)
    raise ApiError(404,"NOT_FOUND","Участник не найден.")


@router.post("/family/leave")
def leave(ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    return family.leave_family(ctx,db)


class Limits(BaseModel):
    food:int=Field(ge=0,le=100_000_000,strict=True)
    home:int=Field(ge=0,le=100_000_000,strict=True)
    kids:int=Field(ge=0,le=100_000_000,strict=True)
    fun:int=Field(ge=0,le=100_000_000,strict=True)


def valid_month(value):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",value) or not 1900<=int(value[:4])<=2200:
        raise ValueError("Месяц должен иметь вид YYYY-MM.")
    return value


class MonthlyIn(BaseModel):
    month:str
    expectedVersion:int=Field(ge=0)
    limits:Limits
    _month=field_validator("month")(valid_month)


@router.put("/monthly-budget")
def monthly(body:MonthlyIn,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    row=db.scalar(select(MonthlyBudget).where(MonthlyBudget.family_id==ctx.family.id,MonthlyBudget.month==body.month))
    if row:
        result=db.execute(update(MonthlyBudget).where(MonthlyBudget.id==row.id,MonthlyBudget.version==body.expectedVersion)
                          .values(version=MonthlyBudget.version+1,limits=body.limits.model_dump()))
        if not result.rowcount:raise ApiError(409,"BUDGET_CONFLICT","Бюджет уже изменён. Загрузите актуальные суммы.")
    else:
        if body.expectedVersion!=0:raise ApiError(409,"BUDGET_CONFLICT","Обновите версию бюджета.")
        row=MonthlyBudget(family_id=ctx.family.id,month=body.month,limits=body.limits.model_dump())
        db.add(row)
        try:db.flush()
        except IntegrityError:
            db.rollback();raise ApiError(409,"BUDGET_CONFLICT","Бюджет уже создал другой участник.")
    log_event(db,"monthly_budget_updated",family_id=ctx.family.id,user_id=ctx.user.id,month=body.month)
    db.commit();db.refresh(row)
    return {"budget":{"month":row.month,"version":row.version,"limits":row.limits}}


class PlanItemIn(BaseModel):
    category:Literal["sections","clubs","allowance"]
    amount:int=Field(ge=0,le=100_000_000,strict=True)
    due:date


class PlanIn(BaseModel):
    childId:str
    month:str
    expectedVersion:int=Field(ge=0)
    items:list[PlanItemIn]=Field(max_length=3)
    _month=field_validator("month")(valid_month)


@router.put("/plans")
def save_plan(body:PlanIn,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    ch=find_child(db,body.childId,ctx.family.id)
    if len({i.category for i in body.items})!=len(body.items):raise ApiError(400,"VALIDATION","Категории повторяются.")
    if any(i.due.strftime("%Y-%m")!=body.month for i in body.items):raise ApiError(400,"VALIDATION","Дата должна входить в месяц плана.")
    plan=db.scalar(select(BudgetPlan).where(BudgetPlan.child_id==ch.id,BudgetPlan.month==body.month))
    previous={i.category:i for i in plan.items} if plan else {}
    items=[budget.ItemIn(id=previous[TO_DOMAIN[i.category]].id if TO_DOMAIN[i.category] in previous else None,
                        category=TO_DOMAIN[i.category],title=TITLES[i.category],amount=i.amount,due_date=i.due)
           for i in body.items if i.amount>0]
    if plan:
        budget.update_plan(plan.id,budget.PlanUpdateIn(version=body.expectedVersion,items=items),ctx,db)
    else:
        if body.expectedVersion!=0:raise ApiError(409,"CONFLICT","План изменён. Обновите страницу.")
        result=budget.create_plan(ch.id,budget.PlanCreateIn(month=body.month,items=items),ctx,db)
        plan=db.get(BudgetPlan,result["id"])
    return {"plan":plan_out(db,plan)}


@router.post("/plans/{plan_id}/{decision}")
def review_plan(plan_id:str,decision:Literal["submit","approve","return"],body:budget.VersionIn,
                ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    plan=budget._get_plan(db,ctx,number(plan_id))
    if decision!="submit" and plan.submitted_by==ctx.user.id:
        raise ApiError(403,"SELF_APPROVE","Согласовать должен второй взрослый.")
    {"submit":budget.submit_plan,"approve":budget.approve_plan,"return":budget.return_plan}[decision](plan.id,body,ctx,db)
    db.refresh(plan)
    return {"plan":plan_out(db,plan)}


class ChildSettingsIn(BaseModel):
    limit:int=Field(ge=0,le=100_000_000,strict=True)
    frozen:bool


@router.put("/children/{external}")
def settings(external:str,body:ChildSettingsIn,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    ch=find_child(db,external,ctx.family.id)
    children.update_child_settings(ch.id,children.ChildSettingsIn(monthly_limit=body.limit,frozen=body.frozen),ctx,db)
    return {"ok":True}


class PaymentIn(BaseModel):
    kind:Literal["expense","child_purchase","allowance","topup","goal"]
    sourceId:str
    childId:str=""
    amount:int=Field(gt=0,le=100_000_000,strict=True)
    title:str=Field(default="Учебная операция",max_length=100)
    category:str="food"
    reminderId:str=""
    idempotencyKey:str=Field(min_length=1,max_length=100)
    simulate:Literal["success","decline","delay"]="success"


@router.post("/payments")
def payment(body:PaymentIn,sess:AuthSession=Depends(get_auth_session),db:Session=Depends(get_db)):
    if body.simulate!="success":demo_enabled()
    ch=None;ctx=None
    if sess.child_id:
        ch=db.get(Child,sess.child_id)
        if body.kind!="child_purchase" or body.childId!=cid(ch) or body.sourceId!="child:"+cid(ch):
            raise ApiError(403,"FORBIDDEN","Ребёнку доступны только собственные покупки.")
        fam_id=ch.family_id;actor=cid(ch)
    else:
        ctx=require_member(db.get(User,sess.user_id),db)
        fam_id=ctx.family.id;actor=uid(db,ctx.user.id)
        if body.childId:ch=find_child(db,body.childId,fam_id)
        if body.kind in ("allowance","child_purchase") and not ch:raise ApiError(400,"VALIDATION","Выберите ребёнка.")
        if body.kind=="child_purchase" and body.sourceId!="child:"+cid(ch):raise ApiError(400,"VALIDATION","Неверный источник детской покупки.")
    if body.category not in {c["id"] for c in CATEGORIES}:raise ApiError(400,"VALIDATION","Неизвестная категория.")
    payload=body.model_dump()
    fingerprint=request_hash(payload)
    e=db.scalar(select(UiOperation).where(UiOperation.actor==actor,UiOperation.key==body.idempotencyKey))
    if e:
        if e.family_id!=fam_id or e.fingerprint!=fingerprint:raise ApiError(409,"CONFLICT","Этот ключ уже использован для другого запроса.")
        return {"operation":operation_out(db,e)}

    # Валидируем права/ссылки до резервирования ключа.
    source=None;target=None;rem=None
    if body.kind!="child_purchase":
        source_id=account_id(db,body.sourceId,ctx)
        source=accessible_account(db,ctx,ctx.user.id,source_id)
        if source.kind!="shared":raise ApiError(403,"FORBIDDEN","Выберите общий счёт.")
        if body.kind=="topup":
            target=source
            source=db.scalar(select(Account).where(Account.kind=="personal",Account.owner_user_id==ctx.user.id).order_by(Account.id))
            if not source:raise ApiError(404,"NOT_FOUND","Ваш личный счёт не найден.")
        elif body.kind=="goal":
            goal=db.get(Goal,fam_id)
            if not goal:
                target=Account(kind="shared",name="Накопительная цель",family_id=fam_id,balance=0)
                db.add(target);db.flush();goal=Goal(family_id=fam_id,account_id=target.id);db.add(goal);db.flush()
            else:target=db.get(Account,goal.account_id)
        if body.reminderId:
            if body.kind not in ("expense","allowance"):raise ApiError(400,"VALIDATION","Выберите оплату или перевод ребёнку.")
            rem=budget._get_reminder(db,ctx,number(body.reminderId))
            item=db.get(BudgetItem,rem.budget_item_id) if rem.budget_item_id else None
            category=db.get(ReminderCategory,rem.id)
            allowance=(item and item.category=="pocket_money") or (category and category.category=="allowance")
            if bool(allowance)!=(body.kind=="allowance"):
                raise ApiError(400,"VALIDATION","Для карманных денег нужен перевод ребёнку, для покупки — оплата расхода.")
            if body.amount!=rem.amount or (ch and ch.id!=rem.child_id):raise ApiError(400,"VALIDATION","Сумма или ребёнок не совпадает с напоминанием.")
            ch=db.get(Child,rem.child_id)
            payload["childId"]=cid(ch)

    e=UiOperation(family_id=fam_id,actor=actor,key=body.idempotencyKey,payload=payload,fingerprint=fingerprint)
    db.add(e)
    try:
        db.flush();log_event(db,"ui_payment_requested",family_id=fam_id,operation_id=e.id);db.commit()
    except IntegrityError:
        db.rollback()
        e=db.scalar(select(UiOperation).where(UiOperation.actor==actor,UiOperation.key==body.idempotencyKey))
        if not e or e.fingerprint!=fingerprint:raise ApiError(409,"CONFLICT","Ключ уже используется другим запросом.")
        return {"operation":operation_out(db,e)}
    try:
        if body.kind=="child_purchase":
            result=children.purchase_from_wallet(db,ch,body.amount,body.title,f"ui:{e.id}",
                        by_user_id=sess.user_id,simulate_failure=body.simulate=="decline")
            e.purchase_id=result["id"]
        else:
            req=PaymentRequest(kind={"expense":"purchase","allowance":"child_topup","topup":"shared_topup","goal":"shared_topup"}[body.kind],
                account_id=source.id,target_account_id=target.id if target else None,
                amount=body.amount,idempotency_key=f"ui:{e.id}",purpose=body.title,
                child_id=ch.id if ch else None,reminder_id=rem.id if rem else None,simulate_failure=body.simulate=="decline")
            p,_=execute_payment(db,ctx,req);e.payment_id=p.id
        log_event(db,"ui_payment_finished",family_id=fam_id,operation_id=e.id)
        db.commit()
    except ApiError as error:
        db.rollback();e=db.get(UiOperation,e.id);e.error=error.detail["message"]
        log_event(db,"ui_payment_rejected",family_id=fam_id,operation_id=e.id);db.commit()
    if body.simulate=="delay":time.sleep(6)  # задержка ответа; операция уже зафиксирована в БД
    return {"operation":operation_out(db,e)}


@router.get("/operations/lookup")
def lookup(key:str,sess:AuthSession=Depends(get_auth_session),db:Session=Depends(get_db)):
    if sess.child_id:
        ch=db.get(Child,sess.child_id);actor=cid(ch);fam_id=ch.family_id
    else:
        ctx=require_member(db.get(User,sess.user_id),db);actor=uid(db,ctx.user.id);fam_id=ctx.family.id
    e=db.scalar(select(UiOperation).where(UiOperation.actor==actor,UiOperation.key==key,UiOperation.family_id==fam_id))
    return {"operation":operation_out(db,e) if e else None}


class ReminderIn(BaseModel):
    id:str=""
    title:str=Field(min_length=1,max_length=100)
    childId:str
    category:str="sections"
    amount:int=Field(gt=0,le=100_000_000,strict=True)
    due:date
    url:str=""
    @field_validator("url")
    @classmethod
    def safe_url(cls,v):
        if not v:return v
        p=urlsplit(v)
        if p.scheme!="https" or not p.hostname or p.username or p.password:raise ValueError("Нужна HTTPS ссылка без логина и пароля.")
        return v


@router.post("/reminders")
def reminder(body:ReminderIn,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    ch=find_child(db,body.childId,ctx.family.id)
    if body.category not in {c["id"] for c in CATEGORIES}|{"allowance"}:raise ApiError(400,"VALIDATION","Неизвестная категория.")
    if body.id:
        r=budget._get_reminder(db,ctx,number(body.id))
        if r.status!="open":raise ApiError(409,"CONFLICT","Закрытое напоминание менять нельзя.")
        if db.scalar(select(Payment.id).where(Payment.reminder_id==r.id,Payment.status=="pending")):
            raise ApiError(409,"CONFLICT","Дождитесь результата оплаты.")
        r.title,r.amount,r.child_id,r.due_date,r.payee_url=body.title,body.amount,ch.id,body.due,body.url or None
        log_event(db,"reminder_updated",family_id=ctx.family.id,user_id=ctx.user.id,reminder_id=r.id);db.commit()
    else:
        result=budget.create_reminder(budget.ReminderIn(child_id=ch.id,title=body.title,amount=body.amount,
                                       due_date=body.due,payee_url=body.url or None),ctx,db)
        r=db.get(Reminder,result["id"])
    category=db.get(ReminderCategory,r.id)
    if not category:category=ReminderCategory(reminder_id=r.id,category=body.category);db.add(category)
    else:category.category=body.category
    db.commit()
    return {"reminder":reminder_out(db,r)}


@router.post("/reminders/{external}/manual")
def manual(external:str,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    budget.mark_done(number(external),ctx,db)
    return {"ok":True}


class NoteIn(BaseModel):
    id:str=""
    title:str=Field(min_length=1,max_length=100)
    text:str=Field(default="",max_length=1000)
    done:bool=False
    authorId:str=""  # совместимость с toggle; автор не берётся из запроса


@router.post("/notes")
def note(body:NoteIn,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    n=db.get(Note,number(body.id)) if body.id else None
    if body.id and (not n or n.family_id!=ctx.family.id):raise ApiError(404,"NOT_FOUND","Заметка не найдена.")
    if not n:n=Note(family_id=ctx.family.id,author_id=ctx.user.id);db.add(n)
    n.title,n.text,n.done=body.title.strip(),body.text,body.done
    log_event(db,"note_updated",family_id=ctx.family.id,user_id=ctx.user.id);db.commit()
    return {"note":{"id":str(n.id),"title":n.title,"text":n.text,"done":n.done,"authorId":uid(db,n.author_id)}}


@router.delete("/notes/{external}")
def delete_note(external:str,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    n=db.get(Note,number(external))
    if not n or n.family_id!=ctx.family.id:raise ApiError(404,"NOT_FOUND","Заметка не найдена.")
    db.delete(n);log_event(db,"note_deleted",family_id=ctx.family.id,user_id=ctx.user.id);db.commit()
    return {"ok":True}


class AccountIn(BaseModel):
    name:str=Field(min_length=1,max_length=45)


@router.post("/accounts")
def new_account(body:AccountIn,ctx:MemberCtx=Depends(require_owner),db:Session=Depends(get_db)):
    if not body.name.strip():raise ApiError(400,"VALIDATION","Введите название.")
    acc=Account(kind="shared",name=body.name.strip(),family_id=ctx.family.id,balance=0)
    db.add(acc);db.flush()
    for m in db.scalars(select(FamilyMember).where(FamilyMember.family_id==ctx.family.id,FamilyMember.status=="active")):
        db.add(Card(account_id=acc.id,holder_user_id=m.user_id,pan_masked=auth.random_pan()))
    log_event(db,"account_created",family_id=ctx.family.id,user_id=ctx.user.id);db.commit()
    return {"account":public_account(db,acc,ctx.family.id)}


class OfferIn(BaseModel):
    id:str=Field(min_length=1,max_length=40)


@router.post("/offers/save")
def save_offer(body:OfferIn,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    row=db.scalar(select(SavedOffer).where(SavedOffer.family_id==ctx.family.id,SavedOffer.offer_id==body.id))
    if row:db.delete(row)
    else:db.add(SavedOffer(family_id=ctx.family.id,offer_id=body.id))
    log_event(db,"offer_saved",family_id=ctx.family.id,user_id=ctx.user.id);db.commit()
    return {"ok":True}


class GoalIn(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    target:int=Field(gt=0,le=100_000_000,strict=True)


@router.put("/goal")
def update_goal(body:GoalIn,ctx:MemberCtx=Depends(require_member),db:Session=Depends(get_db)):
    goal=db.get(Goal,ctx.family.id)
    if not goal:
        a=Account(kind="shared",name="Накопительная цель",family_id=ctx.family.id,balance=0);db.add(a);db.flush()
        goal=Goal(family_id=ctx.family.id,account_id=a.id);db.add(goal)
    if body.target<db.get(Account,goal.account_id).balance:raise ApiError(400,"VALIDATION","Цель меньше накопленной суммы.")
    goal.name,goal.target=body.name,body.target
    log_event(db,"goal_updated",family_id=ctx.family.id,user_id=ctx.user.id);db.commit()
    return {"ok":True}


@router.get("/demo/export")
def export(ctx:MemberCtx=Depends(require_owner),db:Session=Depends(get_db)):
    return {"snapshot":snapshot_data(db,AuthSession(user_id=ctx.user.id)),
            "events":[{"type":e.type,"at":e.created_at.isoformat()+"Z"} for e in db.scalars(select(Event).where(Event.family_id==ctx.family.id))]}


@router.post("/demo/reset",dependencies=[Depends(demo_enabled)])
def reset(ctx:MemberCtx=Depends(require_owner),db:Session=Depends(get_db)):
    if ctx.user.email!="alex@test.ru":raise ApiError(403,"FORBIDDEN","Сброс доступен только учебной семье.")
    fid=ctx.family.id
    child_ids=list(db.scalars(select(Child.id).where(Child.family_id==fid)))
    rids=select(Reminder.id).where(Reminder.family_id==fid)
    pids=select(BudgetPlan.id).where(BudgetPlan.family_id==fid)
    db.execute(delete(UiOperation).where(UiOperation.family_id==fid))
    db.execute(delete(Operation).where(Operation.family_id==fid))
    # Личные исходящие переводы этой семьи тоже ссылаются на Payment.
    pay_ids=select(Payment.id).where(Payment.family_id==fid)
    db.execute(delete(Operation).where(Operation.payment_id.in_(pay_ids)))
    db.execute(delete(Payment).where(Payment.family_id==fid))
    db.execute(delete(ReminderCategory).where(ReminderCategory.reminder_id.in_(rids)))
    db.execute(delete(Reminder).where(Reminder.family_id==fid))
    db.execute(delete(BudgetItem).where(BudgetItem.plan_id.in_(pids)))
    db.execute(delete(BudgetPlan).where(BudgetPlan.family_id==fid))
    db.execute(delete(ChildPurchase).where(ChildPurchase.child_id.in_(child_ids)))
    for model in (Invitation,Note,MonthlyBudget,SavedOffer):
        db.execute(delete(model).where(model.family_id==fid))
    goal=db.get(Goal,fid)
    first=db.scalar(select(Account.id).where(Account.family_id==fid,Account.kind=="shared").order_by(Account.id))
    db.execute(update(Account).where(Account.family_id==fid).values(balance=0))
    db.execute(update(Account).where(Account.id==first).values(balance=800_000))
    if goal:db.execute(update(Account).where(Account.id==goal.account_id).values(balance=6_850_000))
    for email,bal in [("alex@test.ru",8_460_000),("maria@test.ru",1_420_000)]:
        user=db.scalar(select(User).where(User.email==email))
        if user:db.execute(update(Account).where(Account.owner_user_id==user.id,Account.kind=="personal").values(balance=bal))
    for ch in db.scalars(select(Child).where(Child.family_id==fid)):
        ch.balance=0 if cid(ch)=="max" else 180_000;ch.monthly_limit=100_000 if cid(ch)=="max" else 300_000;ch.frozen=False
    for m in db.scalars(select(FamilyMember).where(FamilyMember.family_id==fid,FamilyMember.role!="owner")):
        m.status="removed";m.ended_at=utcnow();family._block_shared_cards(db,fid,m.user_id)
    today=date.today()
    db.add(MonthlyBudget(family_id=fid,month=today.strftime("%Y-%m"),limits={"food":3_500_000,"home":3_000_000,"kids":2_000_000,"fun":1_500_000}))
    db.add(Note(family_id=fid,author_id=ctx.user.id,title="Планы на выходные",text="Обсудить семейный поход в кино и выбрать сеанс."))
    log_event(db,"demo_reset",family_id=fid,user_id=ctx.user.id);db.commit()
    return {"ok":True}
