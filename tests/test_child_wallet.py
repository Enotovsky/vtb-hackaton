from tests.conftest import Api, key


def child_device(family):
    mom = family["mom"]
    code = mom.get(f"/api/children/{family['child']['id']}").json()["access_code"]
    api = Api()
    api.token = api.post("/api/auth/child-login", {"code": code}).json()["token"]
    return api


def topup(family, amount):
    r = family["mom"].post(f"/api/children/{family['child']['id']}/topup",
                           {"account_id": family["shared"]["id"], "amount": amount}, idem=key())
    assert r.json()["status"] == "completed", r.text


def buy(kid, amount, idem=None):
    return kid.post("/api/child/purchases", {"amount": amount, "merchant": "Столовая"}, idem=idem or key()).json()


def test_child_purchase_happy_path(family):
    topup(family, 500_00)
    kid = child_device(family)
    assert buy(kid, 200_00)["status"] == "completed"
    me = kid.get("/api/child/me").json()
    assert me["balance"] == 300_00 and me["spent_this_month"] == 200_00
    # ребёнку недоступны взрослые API
    assert kid.get("/api/family").status_code == 403
    assert kid.get("/api/accounts").status_code == 403


def test_frozen_wallet_rejects_purchase(family):
    topup(family, 500_00)
    kid = child_device(family)
    family["dad"].patch(f"/api/children/{family['child']['id']}/settings", {"frozen": True})
    r = buy(kid, 100)
    assert r["status"] == "failed" and r["failure_reason"] == "wallet_frozen"
    assert kid.get("/api/child/me").json()["balance"] == 500_00


def test_limit_exceeded(family):
    topup(family, 5000_00)  # денег больше, чем лимит (1000 ₽)
    kid = child_device(family)
    assert buy(kid, 800_00)["status"] == "completed"
    r = buy(kid, 300_00)
    assert r["status"] == "failed" and r["failure_reason"] == "limit_exceeded"
    assert kid.get("/api/child/me").json()["balance"] == 4200_00


def test_insufficient_child_balance(family):
    topup(family, 100_00)
    kid = child_device(family)
    r = buy(kid, 200_00)
    assert r["failure_reason"] == "insufficient_funds"


def test_topup_does_not_reset_used_limit(family):
    topup(family, 1000_00)
    kid = child_device(family)
    assert buy(kid, 900_00)["status"] == "completed"
    topup(family, 1000_00)
    me = kid.get("/api/child/me").json()
    assert me["spent_this_month"] == 900_00 and me["limit_left"] == 100_00
    assert buy(kid, 200_00)["failure_reason"] == "limit_exceeded"


def test_raising_limit_adds_no_money(family):
    topup(family, 100_00)
    kid = child_device(family)
    family["mom"].patch(f"/api/children/{family['child']['id']}/settings", {"monthly_limit": 100_000_00})
    me = kid.get("/api/child/me").json()
    assert me["balance"] == 100_00 and me["monthly_limit"] == 100_000_00
    assert buy(kid, 200_00)["failure_reason"] == "insufficient_funds"


def test_parent_paying_section_does_not_use_child_limit(family):
    mom, child = family["mom"], family["child"]
    mom.post("/api/payments", {"account_id": family["shared"]["id"], "amount": 5000_00, "child_id": child["id"],
                               "purpose": "Футбол"}, idem=key())
    c = mom.get(f"/api/children/{child['id']}").json()
    assert c["spent_this_month"] == 0 and c["balance"] == 0


def test_child_double_click(family):
    topup(family, 500_00)
    kid = child_device(family)
    k = key()
    a, b = buy(kid, 100_00, k), buy(kid, 100_00, k)
    assert a["id"] == b["id"] and b["replayed"]
    assert kid.get("/api/child/me").json()["balance"] == 400_00


def test_topup_fails_on_insufficient_parent_funds(family):
    r = family["mom"].post(f"/api/children/{family['child']['id']}/topup",
                           {"account_id": family["shared"]["id"], "amount": 10**12}, idem=key()).json()
    assert r["status"] == "failed"
    assert family["mom"].get(f"/api/children/{family['child']['id']}").json()["balance"] == 0


def test_regenerated_code_revokes_child_session(family):
    kid = child_device(family)
    family["mom"].post(f"/api/children/{family['child']['id']}/access-code")
    assert kid.get("/api/child/me").status_code == 401


def test_parent_test_purchase_uses_same_checks(family):
    mom, cid = family["mom"], family["child"]["id"]
    topup(family, 500_00)
    k = key()
    a = mom.post(f"/api/children/{cid}/test-purchase", {"amount": 200_00}, idem=k).json()
    b = mom.post(f"/api/children/{cid}/test-purchase", {"amount": 200_00}, idem=k).json()
    assert a["status"] == "completed" and b["replayed"] and a["id"] == b["id"]
    c = mom.get(f"/api/children/{cid}").json()
    assert c["balance"] == 300_00 and c["spent_this_month"] == 200_00
    assert mom.post(f"/api/children/{cid}/test-purchase", {"amount": 900_00}, idem=key()).json()["failure_reason"] \
        == "limit_exceeded"
    mom.patch(f"/api/children/{cid}/settings", {"frozen": True})
    assert mom.post(f"/api/children/{cid}/test-purchase", {"amount": 1}, idem=key()).json()["failure_reason"] \
        == "wallet_frozen"
