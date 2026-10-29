import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import ApiError, MemberCtx, require_member
from app.db import get_db
from app.models import BugReport, Event, Payment
from app.serializers import child_name, user_name

router = APIRouter(prefix="/api", tags=["events, metrics, bugs"])

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "uploads"))


@router.get("/family/events")
def family_events(ctx: MemberCtx = Depends(require_member), db: Session = Depends(get_db)):
    evs = db.scalars(select(Event).where(Event.family_id == ctx.family.id).order_by(Event.id.desc()).limit(300))
    return [{
        "id": e.id, "type": e.type, "at": e.created_at.isoformat(),
        "actor": user_name(db, e.actor_user_id) or child_name(db, e.actor_child_id), "payload": e.payload,
    } for e in evs]


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    """Агрегаты по всем семьям для продуктовых метрик (воронка приглашений, согласования, оплаты)."""
    counts = dict(db.execute(select(Event.type, func.count()).group_by(Event.type)).all())
    sent = counts.get("invitation_sent", 0)
    pays = dict(db.execute(select(Payment.status, func.count()).group_by(Payment.status)).all())
    return {
        "events": counts,
        "invite_acceptance_rate": round(counts.get("invitation_accepted", 0) / sent, 3) if sent else None,
        "plans_submitted": counts.get("plan_submitted", 0),
        "plans_approved": counts.get("plan_approved", 0),
        "payments_by_status": pays,
    }


# ---------------- баг-репорты (шаги / ожидаемое / фактическое / скриншот) ----------------

@router.post("/bugs")
async def create_bug(
    title: str = Form(...),
    steps: str = Form(...),
    expected: str = Form(...),
    actual: str = Form(...),
    reporter: str | None = Form(default=None),
    screenshot: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    path = None
    if screenshot and screenshot.filename:
        ext = Path(screenshot.filename).suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            raise ApiError(400, "bad_screenshot", "Скриншот должен быть картинкой")
        UPLOAD_DIR.mkdir(exist_ok=True)
        name = f"{uuid.uuid4().hex}{ext}"
        (UPLOAD_DIR / name).write_bytes(await screenshot.read())
        path = f"/uploads/{name}"
    b = BugReport(title=title, steps=steps, expected=expected, actual=actual, reporter=reporter,
                  screenshot_path=path)
    db.add(b)
    db.commit()
    return _bug_out(b)


@router.get("/bugs")
def list_bugs(db: Session = Depends(get_db)):
    return [_bug_out(b) for b in db.scalars(select(BugReport).order_by(BugReport.id.desc()))]


def _bug_out(b: BugReport) -> dict:
    return {"id": b.id, "title": b.title, "steps": b.steps, "expected": b.expected, "actual": b.actual,
            "reporter": b.reporter, "screenshot": b.screenshot_path, "status": b.status,
            "created_at": b.created_at.isoformat()}
