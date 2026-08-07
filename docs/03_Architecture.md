# Architecture

## 1. Обзор

SellerOS построен как модульный monolith с возможностью выделения сервисов по мере роста.

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│   Client    │────▶│   Backend   │────▶│   PostgreSQL     │
│  (Web UI)   │     │   (FastAPI) │     │                  │
└─────────────┘     └──────┬──────┘     └──────────────────┘
                           │
                    ┌──────┴──────┐
                    │  Adapters   │
                    │ WB/Ozon/YM  │
                    └─────────────┘
```

## 2. Компоненты

### 2.1 Backend (`backend/`)

- **API layer** — REST endpoints, валидация, auth
- **Services** — бизнес-логика
- **Adapters** — интеграции с маркетплейсами
- **Models** — ORM-модели SQLAlchemy

### 2.2 Database (`database/`)

- Миграции Alembic
- Seed-данные для dev/staging
- Схема описана в `06_Database.md`

### 2.3 Scripts (`scripts/`)

- Утилиты деплоя, миграций, импорта данных

## 3. Технологический стек

| Слой | Технология |
|------|------------|
| Backend | Python 3.11+, FastAPI |
| ORM | SQLAlchemy 2.x |
| DB | PostgreSQL 16 |
| Migrations | Alembic |
| Auth | JWT + refresh tokens |
| Cache (future) | Redis |
| Queue (future) | Celery / ARQ |

## 4. Принципы

- **Adapter pattern** для маркетплейсов — единый интерфейс, разные реализации
- **Repository pattern** для доступа к данным
- **Dependency injection** через FastAPI Depends
- **12-factor app** — конфиг через env, stateless processes

## 5. Безопасность

- Credentials маркетплейсов — encrypted at rest
- HTTPS only в production
- Rate limiting на API
- Audit log для критичных операций

## 6. Масштабирование (future)

1. Read replicas для PostgreSQL
2. Redis для кэша и сессий
3. Background workers для sync с маркетплейсами
4. Выделение adapter-сервисов в отдельные контейнеры
