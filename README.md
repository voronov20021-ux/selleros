# SellerOS

Операционная система для продавцов на маркетплейсах: аналитика, автоматизация, управление товарами и продажами.

## Структура проекта

```
SellerOS/
├── backend/      # Серверная логика и API
├── database/     # Схемы, миграции, seed-данные
├── docs/         # Документация проекта
├── prompts/      # Промпты для AI-агентов
├── tests/        # Тесты
├── assets/       # Статические ресурсы
├── scripts/      # Утилиты и скрипты автоматизации
├── requirements.txt
└── .env.example
```

## Быстрый старт

1. Скопируйте `.env.example` в `.env` и заполните переменные окружения.
2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Ознакомьтесь с документацией в папке `docs/`.

## Документация

| Файл | Описание |
|------|----------|
| [01_PRD.md](docs/01_PRD.md) | Product Requirements Document |
| [02_Roadmap.md](docs/02_Roadmap.md) | Дорожная карта |
| [03_Architecture.md](docs/03_Architecture.md) | Архитектура системы |
| [PROJECT_RULES.md](docs/PROJECT_RULES.md) | Правила проекта |

## Telegram Mini App (GitHub Pages)

Фронтенд: `frontend/seller_dashboard`. Команды деплоя, переменная `VITE_API_BASE` и поля BotFather — в [frontend/seller_dashboard/README.md](frontend/seller_dashboard/README.md).

Публичный URL (после вашего commit + push в `main` и успешного GitHub Actions): **https://voronov20021-ux.github.io/selleros/**

В репозитории: **Settings → Pages → Source = GitHub Actions**.

## Лицензия

Proprietary. All rights reserved.
