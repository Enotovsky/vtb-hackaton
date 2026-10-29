import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


class Api:
    """Клиент от имени одного «устройства»."""

    def __init__(self, token=None):
        self.c = TestClient(app)
        self.token = token

    def _h(self, idem=None):
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    def get(self, url, **kw):
        return self.c.get(url, headers=self._h(), **kw)

    def post(self, url, json=None, idem=None, **kw):
        return self.c.post(url, json=json, headers=self._h(idem), **kw)

    def put(self, url, json=None):
        return self.c.put(url, json=json, headers=self._h())

    def patch(self, url, json=None):
        return self.c.patch(url, json=json, headers=self._h())

    def delete(self, url):
        return self.c.delete(url, headers=self._h())


def register(email, name="User"):
    api = Api()
    r = api.post("/api/auth/register", {"email": email, "name": name, "password": "pass1234"})
    assert r.status_code == 200, r.text
    api.token = r.json()["token"]
    return api


def key():
    return uuid.uuid4().hex


@pytest.fixture
def family():
    """Владелец (мама) + принятый взрослый (папа) + ребёнок."""
    mom = register("mom@test.ru", "Мама")
    dad = register("dad@test.ru", "Папа")
    assert mom.post("/api/family", {"name": "Ивановы"}).status_code == 200
    inv = mom.post("/api/family/invitations", {"email": "dad@test.ru"}).json()
    assert dad.post(f"/api/invitations/{inv['token']}/accept").status_code == 200
    child = mom.post("/api/family/children", {"name": "Петя", "monthly_limit": 1000_00}).json()
    shared = next(a for a in mom.get("/api/accounts").json() if a["kind"] == "shared")
    return {"mom": mom, "dad": dad, "child": child, "shared": shared, "invite": inv}
