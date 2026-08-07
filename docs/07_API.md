# API Specification

## 1. Общие сведения

- **Base URL:** `/api/v1`
- **Format:** JSON
- **Auth:** Bearer JWT в заголовке `Authorization`
- **Errors:** RFC 7807 Problem Details (планируется)

## 2. Auth

### POST /auth/register

Регистрация нового пользователя.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "securepassword",
  "name": "Иван"
}
```

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "name": "Иван"
}
```

### POST /auth/login

**Response:** `200 OK`
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer"
}
```

### POST /auth/refresh

Обновление access token по refresh token.

## 3. Shops

### GET /shops

Список подключённых магазинов пользователя.

### POST /shops

Подключение нового магазина.

**Request:**
```json
{
  "marketplace": "wb",
  "name": "Мой магазин WB",
  "api_key": "..."
}
```

### DELETE /shops/{shop_id}

Отключение магазина.

## 4. Products

### GET /shops/{shop_id}/products

Query: `page`, `limit`, `search`

### GET /shops/{shop_id}/products/{product_id}

Детали товара.

## 5. Orders

### GET /shops/{shop_id}/orders

Query: `date_from`, `date_to`, `status`, `page`, `limit`

## 6. Analytics

### GET /shops/{shop_id}/analytics/summary

Query: `date_from`, `date_to`

**Response:**
```json
{
  "revenue": 150000.00,
  "orders_count": 42,
  "avg_order_value": 3571.43,
  "returns_count": 3
}
```

## 7. Sync

### POST /shops/{shop_id}/sync

Запуск ручной синхронизации с маркетплейсом.

**Response:** `202 Accepted`
```json
{
  "job_id": "uuid",
  "status": "queued"
}
```

## 8. Health

### GET /health

**Response:** `200 OK` — `{"status": "ok"}`
