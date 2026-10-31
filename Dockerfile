FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Moscow \
    DATABASE_URL=sqlite:////app/data/family_bank.db \
    UPLOAD_DIR=/app/uploads

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Каталоги под том создаём в образе: иначе том получит владельца root и приложение не сможет писать.
RUN useradd --system --uid 1000 app \
    && mkdir -p /app/data /app/uploads \
    && chown -R app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]
