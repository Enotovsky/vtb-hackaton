# Развёртывание на сервере

Проект собирается в один контейнер: FastAPI раздаёт и API, и фронтенд, данные лежат в SQLite
внутри тома. Внешних сервисов (Postgres, Redis, S3) нет.

| Файл | Зачем |
| --- | --- |
| `Dockerfile` | образ на `python:3.12-slim`, запуск от пользователя `app`, healthcheck по `/health` |
| `docker-compose.yml` | сервис `vtb-family`, два тома, лимит на размер логов |
| `.dockerignore` | в образ не попадают `.venv`, `data/`, `uploads/`, `__pycache__`, `.env` |
| `.env.example` | заготовка `.env` с портом |

## Первый запуск

```bash
cd /root
git clone https://github.com/Enotovsky/vtb-hackaton.git vtb-family
cd vtb-family
cp .env.example .env
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
curl -s localhost:8010/health
```

Сайт: `http://<ip-сервера>:8010`. Учебные профили — Александр, Мария, Макс.

**Порт.** По умолчанию 8010, потому что 8000 занят `ecoscan-api`, а 3000/8080 — другими проектами.
Меняется в `.env` (`HTTP_PORT=...`), затем `docker compose up -d`.

**Демо-режим.** `VTB_DEMO_MODE=1` в `.env` включает учебную семью и вход без пароля. Для обычного
входа поставить `0` и пересоздать контейнер.

## Обновление

```bash
cd /root/vtb-family
git pull
docker compose up -d --build
```

Старый образ после пересборки остаётся висеть без тега — почистить: `docker image prune -f`.

## Повседневное

```bash
docker compose logs -f --tail 100    # логи
docker compose restart               # перезапуск
docker compose stop                  # остановить, данные сохранить
docker compose down                  # остановить и удалить контейнер (тома остаются)
docker compose down -v               # ВНИМАНИЕ: вместе с томами, то есть с базой
```

## Бэкап базы

База — SQLite в томе `vtb-family_vtb_family_data`:

```bash
docker compose exec vtb-family \
  python -c "import sqlite3,shutil; shutil.copy('/app/data/family_bank.db','/tmp/backup.db')"
docker compose cp vtb-family:/tmp/backup.db ./family_bank_$(date +%F).db
```

Копировать файл базы «на живую» через `cp` не стоит — из-за WAL копия может оказаться битой.

---

# Чистка Docker, когда кончается место

## 1. Понять, что именно занято

```bash
docker system df
```

Покажет четыре строки — Images, Containers, Local Volumes, Build Cache — и сколько в каждой
можно освободить (колонка RECLAIMABLE). Обычно виноват build cache или старые образы.

Детально, по каждому объекту:

```bash
docker system df -v
```

Место на диске целиком и размер каталога Docker:

```bash
df -h /
du -sh /var/lib/docker/*
```

## 2. Посмотреть контейнеры

```bash
docker ps                 # только запущенные
docker ps -a              # все, включая остановленные
```

Удобнее в виде таблицы:

```bash
docker ps -a -s --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Size}}'
```

Без `-s` колонка `.Size` будет пустой: размер Docker считает только по этому флагу.

Сколько каждый ест памяти и CPU прямо сейчас:

```bash
docker stats --no-stream
```

Какие compose-проекты подняты и из каких каталогов:

```bash
docker compose ls
```

## 3. Выключить ненужные

Разово, по имени:

```bash
docker stop <имя>
```

Весь проект целиком — из его каталога:

```bash
cd /root/<проект> && docker compose down
```

Если контейнер создан с `restart: always`, после `docker stop` он поднимется снова при
перезагрузке сервера. Чтобы этого не было:

```bash
docker update --restart=no <имя>
docker stop <имя>
```

Удалить остановленный контейнер (образ и тома останутся):

```bash
docker rm <имя>
```

## 4. Освободить место

По нарастанию риска.

```bash
docker builder prune -f          # кэш сборки — почти всегда самое жирное, удалять безопасно
docker container prune -f        # остановленные контейнеры
docker image prune -f            # образы без тега (<none>) после пересборок
docker image prune -a -f         # ВСЕ образы, на которые нет запущенных контейнеров
```

`docker image prune -a` снесёт в том числе образы остановленных проектов — при следующем
запуске они будут собираться или качаться заново.

Логи контейнеров (у старых проектов без `max-size` разрастаются до гигабайтов):

```bash
du -sh /var/lib/docker/containers/*/*-json.log | sort -h | tail
truncate -s 0 /var/lib/docker/containers/<id>/<id>-json.log
```

Чтобы не повторялось — добавить в compose каждого проекта блок как в этом:

```yaml
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

## 5. Чего делать не надо, не подумав

```bash
docker volume prune -a -f    # удалит тома, не привязанные к контейнерам
docker system prune -a --volumes -f
```

Это сносит **данные**: базы, загруженные файлы, тома остановленных проектов. Сначала посмотреть,
что именно уйдёт:

```bash
docker volume ls
docker volume ls -f dangling=true     # кандидаты на удаление
```

И только если в списке нет ничего нужного — удалять поимённо:

```bash
docker volume rm <имя_тома>
```

Безопасный вариант «почистить всё лишнее, не трогая данные»:

```bash
docker builder prune -f && docker container prune -f && docker image prune -a -f
```
