from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routers import accounts, auth, budget, children, events, family
from app.routers import ui
from app.core import ApiError
from app.routers.events import UPLOAD_DIR

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="ВТБ Семейный банкинг — MVP", version="0.1.0", lifespan=lifespan)

for r in (auth, family, accounts, children, budget, events):
    app.include_router(r.router)
app.include_router(ui.router)


@app.exception_handler(ApiError)
async def api_error_handler(request, exc):
    detail = exc.detail
    # Старый API сохраняет detail; адаптер отдаёт согласованный контракт error.
    if request.url.path.startswith("/api/ui/"):
        code = "CONFLICT" if detail["code"] in ("version_conflict", "plan_exists") else detail["code"]
        return JSONResponse(status_code=exc.status_code, content={"error": {**detail, "code": code}})
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    if request.url.path.startswith("/api/ui/"):
        return JSONResponse(status_code=422, content={"error": {"code":"VALIDATION", "message":"Проверьте поля формы: суммы, даты и обязательные значения."}})
    from fastapi.exception_handlers import request_validation_exception_handler
    return await request_validation_exception_handler(request, exc)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok", "storage": "sqlite", "payments": "simulated"}


# Корень раздаёт только frontend, а не исходники, БД или конфигурацию.
# Этот mount должен находиться после всех API маршрутов.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
