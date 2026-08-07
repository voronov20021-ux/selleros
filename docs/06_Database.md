# Database Schema

## 1. Обзор

- **СУБД:** PostgreSQL 16
- **ORM:** SQLAlchemy 2.x
- **Миграции:** Alembic (`database/migrations/`)

## 2. ER-диаграмма (концепт)

```
users ──────< shops ──────< products
  │              │
  │              └──────< orders
  │
  └──────< refresh_tokens
```

## 3. Таблицы

### users

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| email | VARCHAR UNIQUE | |
| password_hash | VARCHAR | bcrypt |
| name | VARCHAR | |
| is_active | BOOLEAN | default true |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### shops

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| marketplace | ENUM | wb, ozon, yandex |
| name | VARCHAR | Отображаемое имя |
| credentials_encrypted | TEXT | Зашифрованные API-ключи |
| is_active | BOOLEAN | |
| last_sync_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |

### products

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| shop_id | UUID FK → shops | |
| external_id | VARCHAR | ID на маркетплейсе |
| sku | VARCHAR | |
| name | VARCHAR | |
| price | DECIMAL | |
| stock | INTEGER | |
| metadata | JSONB | Доп. поля от МП |
| updated_at | TIMESTAMPTZ | |

### orders

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| shop_id | UUID FK → shops | |
| external_id | VARCHAR | |
| status | VARCHAR | |
| total_amount | DECIMAL | |
| items | JSONB | Позиции заказа |
| ordered_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |

## 4. Индексы

- `shops(user_id)`
- `products(shop_id, external_id)` UNIQUE
- `orders(shop_id, external_id)` UNIQUE
- `orders(shop_id, ordered_at)`

## 5. Миграции

```bash
# Создать миграцию
alembic revision --autogenerate -m "description"

# Применить
alembic upgrade head
```

## 6. Seed-данные

Dev seed: `database/seeds/dev.sql` (создать при инициализации backend).
