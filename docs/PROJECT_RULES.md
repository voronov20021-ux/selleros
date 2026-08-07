# Project Rules

Правила разработки SellerOS для команды и AI-агентов.

## 1. Код

- Python 3.11+, type hints обязательны для публичных функций
- Форматирование: ruff (lint + format)
- Именование: snake_case (Python), PascalCase (классы)
- Коммиты: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`)

## 2. Архитектура

- Бизнес-логика — в `services/`, не в route handlers
- Доступ к БД — через repositories
- Интеграции с МП — только через adapters (`adapters/wb/`, `adapters/ozon/`)
- Конфиг — через env variables, не hardcode

## 3. База данных

- Любое изменение схемы — Alembic migration
- Не удалять колонки без deprecation period
- Обновлять `06_Database.md` при изменении схемы

## 4. API

- Версионирование: `/api/v1/`
- Breaking changes — новая версия API
- Документировать endpoints в `07_API.md`
- OpenAPI schema генерируется FastAPI автоматически

## 5. Безопасность

- Секреты только в `.env`, никогда в git
- API-ключи маркетплейсов — encrypt at rest
- Валидировать все входные данные (Pydantic)
- Rate limiting на auth endpoints

## 6. Тестирование

- Unit tests для services и adapters
- Integration tests для API endpoints
- Минимальное покрытие: 70% для `backend/`

## 7. Документация

- PRD/Roadmap — обновлять при изменении scope
- Решения — фиксировать в `11_Decision_Log.md`
- README — актуальный quick start

## 8. Git workflow

- `main` — stable, deployable
- Feature branches: `feat/`, `fix/`, `docs/`
- PR обязателен для merge в `main`
- Не force push в `main`

## 9. AI-агенты

- Читать `04_Brain.md` для контекста
- Следовать этому документу
- Не создавать файлы вне согласованной структуры
- Промпты хранить в `prompts/`
