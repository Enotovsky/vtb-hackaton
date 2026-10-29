import time

from tests.conftest import Api, register


def test_owner_creates_family_and_invites(family):
    mom, dad = family["mom"], family["dad"]
    fam = dad.get("/api/family").json()
    assert {m["name"] for m in fam["members"]} == {"Мама", "Папа"}
    assert fam["my_role"] == "adult"
    # приглашает только владелец
    assert dad.post("/api/family/invitations", {"email": "x@test.ru"}).status_code == 403
    assert mom.get("/api/family/invitations").json()[0]["status"] == "accepted"


def test_repeated_accept_does_not_duplicate_member(family):
    mom, dad, inv = family["mom"], family["dad"], family["invite"]
    r = dad.post(f"/api/invitations/{inv['token']}/accept")
    assert r.status_code == 200 and r.json()["already"] is True
    assert len(mom.get("/api/family").json()["members"]) == 2


def test_invitation_statuses_declined_cancelled():
    mom = register("mom@test.ru")
    bob = register("bob@test.ru")
    mom.post("/api/family", {"name": "F"})
    i1 = mom.post("/api/family/invitations", {"email": "bob@test.ru"}).json()
    assert mom.post(f"/api/family/invitations/{i1['id']}/cancel").json()["status"] == "cancelled"
    assert bob.post(f"/api/invitations/{i1['token']}/accept").json()["detail"]["code"] == "invite_cancelled"
    i2 = mom.post("/api/family/invitations", {"email": "bob@test.ru"}).json()
    assert bob.post(f"/api/invitations/{i2['token']}/decline").json()["status"] == "declined"
    assert bob.get("/api/family").status_code == 403
    statuses = [i["status"] for i in mom.get("/api/family/invitations").json()]
    assert statuses == ["declined", "cancelled"]


def test_expired_invitation_cannot_be_accepted():
    mom = register("mom@test.ru")
    bob = register("bob@test.ru")
    mom.post("/api/family", {"name": "F"})
    inv = mom.post("/api/family/invitations", {"email": "bob@test.ru", "ttl_seconds": 1}).json()
    time.sleep(1.2)
    r = bob.post(f"/api/invitations/{inv['token']}/accept")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invite_expired"
    assert mom.get("/api/family/invitations").json()[0]["status"] == "expired"
    assert bob.get("/api/family").status_code == 403


def test_invite_requires_login_and_matching_email(family):
    stranger = register("stranger@test.ru")
    mom = family["mom"]
    mom.delete(f"/api/family/members/{family['dad'].get('/api/auth/me').json()['user']['id']}")
    inv = mom.post("/api/family/invitations", {"email": "dad@test.ru"}).json()
    assert Api().post(f"/api/invitations/{inv['token']}/accept").status_code == 401
    assert stranger.post(f"/api/invitations/{inv['token']}/accept").status_code == 404


def test_removed_member_loses_access_immediately(family):
    mom, dad, shared = family["mom"], family["dad"], family["shared"]
    dad_id = dad.get("/api/auth/me").json()["user"]["id"]
    assert dad.get("/api/family/operations").status_code == 200
    assert mom.delete(f"/api/family/members/{dad_id}").status_code == 200
    # та же сессия, но доступа к семье больше нет
    assert dad.get("/api/family").status_code == 403
    assert dad.get("/api/family/operations").status_code == 403
    assert dad.get(f"/api/accounts/{shared['id']}/operations").status_code == 404
    assert all(a["kind"] == "personal" for a in dad.get("/api/accounts").json())
    # карта папы на общем счёте заблокирована
    cards = next(a for a in mom.get("/api/accounts").json() if a["kind"] == "shared")["cards"]
    assert {c["holder"]: c["status"] for c in cards}["Папа"] == "blocked"


def test_member_leaves(family):
    dad = family["dad"]
    assert dad.post("/api/family/leave").status_code == 200
    assert dad.get("/api/family").status_code == 403
    assert family["mom"].post("/api/family/leave").status_code == 400  # владелец не может уйти


def test_reinvite_after_removal_reuses_member_row(family):
    mom, dad = family["mom"], family["dad"]
    dad_id = dad.get("/api/auth/me").json()["user"]["id"]
    mom.delete(f"/api/family/members/{dad_id}")
    inv = mom.post("/api/family/invitations", {"email": "dad@test.ru"}).json()
    assert dad.post(f"/api/invitations/{inv['token']}/accept").json()["already"] is False
    assert len(mom.get("/api/family").json()["members"]) == 2


def test_logout_revokes_session(family):
    dad = family["dad"]
    dad.post("/api/auth/logout")
    assert dad.get("/api/family").status_code == 401


def test_family_limited_to_two_adults(family):
    r = family["mom"].post("/api/family/invitations", {"email": "third@test.ru"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "family_full"


def test_old_invite_does_not_restore_access_after_removal(family):
    mom, dad, inv = family["mom"], family["dad"], family["invite"]
    dad_id = dad.get("/api/auth/me").json()["user"]["id"]
    mom.delete(f"/api/family/members/{dad_id}")
    r = dad.post(f"/api/invitations/{inv['token']}/accept")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invite_used"
    assert dad.get("/api/family").status_code == 403
