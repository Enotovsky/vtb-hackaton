from concurrent.futures import ThreadPoolExecutor

from tests.conftest import key


def _balance(api, account_id):
    return next(a["balance"] for a in api.get("/api/accounts").json() if a["id"] == account_id)


def _personal(api):
    return next(a for a in api.get("/api/accounts").json() if a["kind"] == "personal")


def test_shared_history_visible_to_both_personal_hidden(family):
    mom, dad, shared, child = family["mom"], family["dad"], family["shared"], family["child"]
    r = dad.post("/api/payments", {"account_id": shared["id"], "amount": 500_00, "purpose": "Продукты"}, idem=key())
    assert r.json()["status"] == "completed"
    # личная покупка мамы
    mom_personal = _personal(mom)
    mom.post("/api/payments", {"account_id": mom_personal["id"], "amount": 100_00, "purpose": "Секрет"}, idem=key())

    ops = mom.get("/api/family/operations").json()
    assert len(ops) == 1
    op = ops[0]
    assert op["payer"] == "Папа" and op["amount"] == -500_00 and op["purpose"] == "Продукты" and op["date"]
    assert op["card"]  # плательщик платил своей картой, привязанной к общему счёту

    # папа не видит личный счёт мамы ни в списке, ни напрямую, ни через платежи
    assert mom_personal["id"] not in [a["id"] for a in dad.get("/api/accounts").json()]
    assert dad.get(f"/api/accounts/{mom_personal['id']}/operations").status_code == 404
    assert "Секрет" not in str(dad.get("/api/payments").json())
    assert "Секрет" not in str(dad.get("/api/family/operations").json())
    assert dad.post("/api/payments", {"account_id": mom_personal["id"], "amount": 1}, idem=key()).status_code == 404
    # общий остаток одинаков у обоих
    assert _balance(mom, shared["id"]) == _balance(dad, shared["id"]) == shared["balance"] - 500_00


def test_double_click_charges_once(family):
    mom, shared = family["mom"], family["shared"]
    k = key()
    body = {"account_id": shared["id"], "amount": 1000_00, "purpose": "Секция"}
    r1 = mom.post("/api/payments", body, idem=k).json()
    r2 = mom.post("/api/payments", body, idem=k).json()
    assert r1["id"] == r2["id"] and r2["replayed"] is True
    assert _balance(mom, shared["id"]) == shared["balance"] - 1000_00


def test_parallel_double_click_charges_once(family):
    mom, shared = family["mom"], family["shared"]
    k = key()
    body = {"account_id": shared["id"], "amount": 100_00, "purpose": "x"}
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda _: mom.post("/api/payments", body, idem=k), range(8)))
    assert len({r.json()["id"] for r in res}) == 1
    assert _balance(mom, shared["id"]) == shared["balance"] - 100_00


def test_idempotency_key_reuse_with_other_params_rejected(family):
    mom, shared = family["mom"], family["shared"]
    k = key()
    mom.post("/api/payments", {"account_id": shared["id"], "amount": 100}, idem=k)
    r = mom.post("/api/payments", {"account_id": shared["id"], "amount": 999}, idem=k)
    assert r.status_code == 409


def test_insufficient_funds_does_not_change_money(family):
    mom, shared = family["mom"], family["shared"]
    r = mom.post("/api/payments", {"account_id": shared["id"], "amount": shared["balance"] + 1}, idem=key()).json()
    assert r["status"] == "failed" and r["failure_reason"] == "insufficient_funds"
    assert _balance(mom, shared["id"]) == shared["balance"]
    assert mom.get("/api/family/operations").json() == []


def test_provider_error_does_not_change_money(family):
    mom, shared = family["mom"], family["shared"]
    r = mom.post("/api/payments", {"account_id": shared["id"], "amount": 100, "simulate_failure": True},
                 idem=key()).json()
    assert r["status"] == "failed" and r["failure_reason"] == "provider_error"
    assert _balance(mom, shared["id"]) == shared["balance"]


def test_payment_stores_payer_and_child_separately(family):
    dad, shared, child = family["dad"], family["shared"], family["child"]
    p = dad.post("/api/payments", {"account_id": shared["id"], "amount": 3000_00, "purpose": "Бассейн",
                                   "child_id": child["id"]}, idem=key()).json()
    assert p["payer"] == "Папа" and p["child"] == "Петя"
    op = dad.get("/api/family/operations").json()[0]
    assert op["payer"] == "Папа" and op["child"] == "Петя"


def test_transfer_personal_to_shared(family):
    dad, shared = family["dad"], family["shared"]
    personal = _personal(dad)
    r = dad.post("/api/transfers", {"from_account_id": personal["id"], "to_account_id": shared["id"],
                                    "amount": 200_00}, idem=key()).json()
    assert r["status"] == "completed"
    assert _balance(dad, shared["id"]) == shared["balance"] + 200_00
    # мама видит пополнение общего счёта, но не личный счёт папы
    ops = family["mom"].get("/api/family/operations").json()
    assert len(ops) == 1 and ops[0]["type"] == "transfer_in" and ops[0]["payer"] == "Папа"
