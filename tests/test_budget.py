from tests.conftest import key

ITEMS = [
    {"category": "section", "title": "Футбол", "amount": 4000_00, "due_date": "2026-11-05",
     "payee_url": "https://example.com/football"},
    {"category": "club", "title": "Робототехника", "amount": 3000_00, "due_date": "2026-11-10"},
    {"category": "pocket_money", "title": "Карманные", "amount": 2000_00},
]


def make_approved_plan(family):
    mom, dad, child = family["mom"], family["dad"], family["child"]
    p = mom.post(f"/api/children/{child['id']}/plans", {"month": "2026-11", "items": ITEMS}).json()
    p = mom.post(f"/api/plans/{p['id']}/submit", {"version": p["version"]}).json()
    p = dad.post(f"/api/plans/{p['id']}/approve", {"version": p["version"]}).json()
    assert p["status"] == "approved"
    return p


def _balance(api, account_id):
    return next(a["balance"] for a in api.get("/api/accounts").json() if a["id"] == account_id)


def test_plan_does_not_reserve_money(family):
    before = _balance(family["mom"], family["shared"]["id"])
    make_approved_plan(family)
    assert _balance(family["mom"], family["shared"]["id"]) == before


def test_submitter_cannot_self_approve_return_flow(family):
    mom, dad, child = family["mom"], family["dad"], family["child"]
    p = mom.post(f"/api/children/{child['id']}/plans", {"month": "2026-12", "items": ITEMS}).json()
    p = mom.post(f"/api/plans/{p['id']}/submit", {"version": p["version"]}).json()
    assert mom.post(f"/api/plans/{p['id']}/approve", {"version": p["version"]}).status_code == 403
    p = dad.post(f"/api/plans/{p['id']}/return", {"version": p["version"], "comment": "Дорого"}).json()
    assert p["status"] == "needs_revision" and p["review_comment"] == "Дорого"


def test_changing_amount_requires_reapproval(family):
    mom = family["mom"]
    p = make_approved_plan(family)
    items = p["items"]
    items[0]["amount"] = 4500_00
    p2 = mom.put(f"/api/plans/{p['id']}", {"version": p["version"], "items": items}).json()
    assert p2["status"] == "draft" and p2["approved_by"] is None


def test_renaming_keeps_approval(family):
    p = make_approved_plan(family)
    items = p["items"]
    items[0]["title"] = "Футбол (ДЮСШ)"
    p2 = family["mom"].put(f"/api/plans/{p['id']}", {"version": p["version"], "items": items}).json()
    assert p2["status"] == "approved"


def test_concurrent_edits_do_not_overwrite(family):
    mom, dad, child = family["mom"], family["dad"], family["child"]
    p = mom.post(f"/api/children/{child['id']}/plans", {"month": "2026-11", "items": ITEMS}).json()
    a = [dict(i) for i in p["items"]]
    b = [dict(i) for i in p["items"]]
    a[0]["amount"] = 1
    b[1]["amount"] = 2
    r1 = mom.put(f"/api/plans/{p['id']}", {"version": p["version"], "items": a})
    r2 = dad.put(f"/api/plans/{p['id']}", {"version": p["version"], "items": b})  # устаревшая версия
    assert r1.status_code == 200 and r2.status_code == 409
    assert r2.json()["detail"]["code"] == "version_conflict"
    cur = mom.get(f"/api/plans/{p['id']}").json()
    assert cur["items"][0]["amount"] == 1 and cur["items"][1]["amount"] == 3000_00


def test_approve_with_stale_version_rejected(family):
    mom, dad, child = family["mom"], family["dad"], family["child"]
    p = mom.post(f"/api/children/{child['id']}/plans", {"month": "2026-11", "items": ITEMS}).json()
    p = mom.post(f"/api/plans/{p['id']}/submit", {"version": p["version"]}).json()
    assert dad.post(f"/api/plans/{p['id']}/approve", {"version": p["version"] - 1}).status_code == 409


def test_reminders_created_and_closed_by_payment(family):
    mom, dad, shared = family["mom"], family["dad"], family["shared"]
    make_approved_plan(family)
    reminders = mom.get("/api/reminders").json()
    assert len(reminders) == 3
    r = next(x for x in reminders if x["title"] == "Футбол")
    assert r["child"] == "Петя" and r["amount"] == 4000_00 and r["budget_item_id"]

    k = key()
    p = dad.post("/api/payments", {"account_id": shared["id"], "reminder_id": r["id"]}, idem=k).json()
    assert p["status"] == "completed" and p["amount"] == 4000_00 and p["child"] == "Петя"
    r2 = next(x for x in mom.get("/api/reminders").json() if x["id"] == r["id"])
    assert r2["status"] == "paid" and r2["payment_id"] == p["id"] and r2["operation_id"]
    # повтор с тем же ключом не списывает, с новым — отклоняется (напоминание закрыто)
    assert dad.post("/api/payments", {"account_id": shared["id"], "reminder_id": r["id"]}, idem=k).json()["replayed"]
    assert dad.post("/api/payments", {"account_id": shared["id"], "reminder_id": r["id"]},
                    idem=key()).status_code == 409
    assert _balance(mom, shared["id"]) == shared["balance"] - 4000_00


def test_failed_payment_keeps_reminder_open(family):
    mom, shared = family["mom"], family["shared"]
    make_approved_plan(family)
    r = mom.get("/api/reminders").json()[0]
    p = mom.post("/api/payments", {"account_id": shared["id"], "reminder_id": r["id"], "simulate_failure": True},
                 idem=key()).json()
    assert p["status"] == "failed"
    assert next(x for x in mom.get("/api/reminders").json() if x["id"] == r["id"])["status"] == "open"


def test_external_site_and_manual_mark_do_not_move_money(family):
    mom, shared = family["mom"], family["shared"]
    make_approved_plan(family)
    before = _balance(mom, shared["id"])
    r = next(x for x in mom.get("/api/reminders").json() if x["title"] == "Футбол")
    assert mom.post(f"/api/reminders/{r['id']}/open-external").json()["url"] == "https://example.com/football"
    assert mom.post(f"/api/reminders/{r['id']}/mark-done").json()["status"] == "marked_done"
    assert _balance(mom, shared["id"]) == before
    assert mom.get("/api/family/operations").json() == []


def test_paid_item_amount_cannot_change(family):
    mom, shared = family["mom"], family["shared"]
    p = make_approved_plan(family)
    r = next(x for x in mom.get("/api/reminders").json() if x["title"] == "Футбол")
    mom.post("/api/payments", {"account_id": shared["id"], "reminder_id": r["id"]}, idem=key())
    items = p["items"]
    items[0]["amount"] = 1
    assert mom.put(f"/api/plans/{p['id']}", {"version": p["version"], "items": items}).status_code == 409


def test_events_logged(family):
    mom, shared = family["mom"], family["shared"]
    make_approved_plan(family)
    mom.post("/api/payments", {"account_id": shared["id"], "amount": 100}, idem=key())
    types = {e["type"] for e in mom.get("/api/family/events").json()}
    assert {"family_created", "invitation_sent", "invitation_accepted", "plan_submitted",
            "plan_approved", "payment_completed"} <= types
    m = mom.get("/api/metrics").json()
    assert m["invite_acceptance_rate"] == 1.0


def test_bug_report_with_screenshot(family):
    r = family["mom"].c.post("/api/bugs", data={"title": "t", "steps": "1. ...", "expected": "e", "actual": "a"},
                             files={"screenshot": ("s.png", b"\x89PNG fake", "image/png")})
    assert r.status_code == 200 and r.json()["screenshot"].startswith("/uploads/")


def test_save_without_changes_keeps_version_and_status(family):
    p = make_approved_plan(family)
    r = family["mom"].put(f"/api/plans/{p['id']}", {"version": p["version"], "items": p["items"]}).json()
    assert r["changed"] is False and r["version"] == p["version"] and r["status"] == "approved"
    # и на этапе согласования тоже: пустое сохранение не мешает согласовать по той же версии
    mom, dad, child = family["mom"], family["dad"], family["child"]
    q = mom.post(f"/api/children/{child['id']}/plans", {"month": "2027-01", "items": ITEMS}).json()
    q = mom.post(f"/api/plans/{q['id']}/submit", {"version": q["version"]}).json()
    mom.put(f"/api/plans/{q['id']}", {"version": q["version"], "items": q["items"]})
    assert dad.post(f"/api/plans/{q['id']}/approve", {"version": q["version"]}).status_code == 200
