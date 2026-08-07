# Deployment

## 1. Окружения

| Окружение | Назначение | URL (план) |
|-----------|------------|------------|
| local | Разработка | localhost:8000 |
| staging | Тестирование | staging.selleros.ru |
| production | Прод | app.selleros.ru |

## 2. Требования

- Docker 24+
- Docker Compose 2.x
- PostgreSQL 16
- Python 3.11+ (для local dev без Docker)

## 3. Переменные окружения

См. `.env.example`. Обязательные для production:

- `SECRET_KEY` — случайная строка ≥ 32 символов
- `DATABASE_URL` — PostgreSQL connection string
- `APP_ENV=production`
- `DEBUG=false`

## 4. Local development

```bash
cp .env.example .env
pip install -r requirements.txt
# Запуск backend (после инициализации)
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## 5. Docker (план)

```bash
docker compose up -d
docker compose exec backend alembic upgrade head
```

## 6. CI/CD (план)

1. Push → GitHub Actions
2. Lint (ruff) + tests (pytest)
3. Build Docker image
4. Deploy to staging (auto)
5. Deploy to production (manual approval)

## 7. Мониторинг (Post-MVP)

- Health checks: `/health`
- Logs: structured JSON → centralized logging
- Metrics: Prometheus + Grafana
- Alerts: uptime, error rate, sync failures

## 8. Backup

- PostgreSQL: daily automated backups, retention 30 days
- Credentials: encrypted, backup отдельно от БД

## 9. Rollback

- Docker: откат на предыдущий image tag
- DB: миграции только forward; rollback через новую миграцию
