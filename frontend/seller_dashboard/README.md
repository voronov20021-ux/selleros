# SellerOS Dashboard (Telegram Mini App)

Vite + React in `src/`. Vanilla `app.js` is **frozen** — do not grow it.

## Auth (required for API data)

1. Client reads `Telegram.WebApp.initData`
2. `POST /api/auth/telegram` with `{ "initData": "..." }`
3. Server validates HMAC and returns `session_token`
4. Subsequent calls send `Authorization: Bearer <session_token>`

`seller_id` = `telegram_user_id`. Path `{seller_id}` must match the session.

Outside Telegram in production: AuthWall. API still rejects unauthenticated access.

`docs/07_API.md` is a stale JWT/email spec — ignore it. Real API: `backend/api/main.py`.

## DEV vs PRODUCTION

### DEV (local Mini App preview)

Synthetic seller `dev-preview` / display name `Local Preview`. Isolated by `seller_id` — cannot see other sellers' MemoryStore rows. Catalog starts empty; add a public WB card via Товары if ProductService works without a WB key. This is **not** a production HMAC bypass.

Backend `POST /api/auth/dev` succeeds only when `MINIAPP_DEV_AUTH` is truthy **and** the request is localhost / 127.0.0.1. If `APP_ENV=production` or `prod`, DEV auth is refused even on localhost. Missing Telegram `initData` never falls through to DEV login.

Frontend calls DEV login only when Vite `import.meta.env.DEV` is true (and `VITE_API_BASE` is empty or loopback). GitHub Pages `PROD` builds do not hit this path.

PowerShell, from repo root:

```powershell
# backend
$env:MINIAPP_DEV_AUTH = "1"
uvicorn backend.api.main:app --reload --port 8000
```

```powershell
# frontend
cd frontend/seller_dashboard
npm run dev
# open http://127.0.0.1:5175/
```

bash:

```bash
# backend
set MINIAPP_DEV_AUTH=1
uvicorn backend.api.main:app --reload --port 8000
# frontend
cd frontend/seller_dashboard
npm run dev
# open http://127.0.0.1:5175/
```

Vite proxies `/api`, `/dashboard`, `/health` to port 8000.

### PRODUCTION

```
MINIAPP_DEV_AUTH=0
# Telegram initData required (POST /api/auth/telegram HMAC)
# VITE_API_BASE=https://...
```

To restore production auth locally: unset or set `MINIAPP_DEV_AUTH=0`, restart uvicorn. Mini App then requires Telegram WebApp `initData` again.

## Run API

From repo root:

```bash
pip install -r requirements.txt
uvicorn backend.api.main:app --reload --port 8000
```

## Run frontend

```bash
cd frontend/seller_dashboard
npm install
npm run dev
```

Open http://127.0.0.1:5175 — Vite proxies `/dashboard`, `/api`, `/health` to port 8000.

## Mini App routes (thin adapters)

- Auth: `POST /api/auth/telegram`, `POST /api/auth/logout`, local-only `POST /api/auth/dev`
- Dashboard: `GET /dashboard/{seller_id}`, `GET .../products`
- Onboarding: `/api/onboarding/*` (WB key never echoed)
- Product first-screen: `GET /api/products/{article}` (Advisor cards, no funnel rewrite)
- Profile / WB status: `GET|POST /api/profile`, `GET /api/wb/status`
- Formula lesson: `GET|POST /api/formula/lesson`
- Actions: `POST /api/actions`, accept/done/defer — IDEA ≠ ACTION until Принять
- Assistant: `POST /api/assistant/chat`
- Missions / time: `/api/missions`, `/api/time/settings`

Action buttons never auto-publish to Wildberries. BOT_TOKEN stays server-side.

## GitHub Pages (production Mini App)

Публичный URL этого репозитория (`voronov20021-ux/selleros`):

**https://voronov20021-ux.github.io/selleros/**

Сайт появится **только после** вашего commit + push в `main` и успешного workflow **Deploy GitHub Pages**. Этот шаг сам по себе ничего не пушит.

SPA стоит в **корне** project-site Pages (`index.html` на `https://voronov20021-ux.github.io/selleros/`). Маршруты — HashRouter (`/#/products`), чтобы GitHub Pages не отдавал 404 при обновлении страницы.

### 1. Локальный production-билд

PowerShell (Windows), из корня репозитория:

```powershell
cd frontend/seller_dashboard
npm install
$env:VITE_BASE = "/selleros/"
$env:VITE_API_BASE = "https://<future-amvera-host>"
npm run build
```

bash:

```bash
cd frontend/seller_dashboard
npm install
VITE_BASE=/selleros/ VITE_API_BASE=https://<future-amvera-host> npm run build
```

Без `VITE_API_BASE` билд **пройдёт**, но в логе будет WARNING: UI откроется, а логин/API не заработают. `localhost` / `127.0.0.1` в `VITE_API_BASE` **запрещены** для `npm run build` — билд упадёт.

Локальный `npm run dev` не ставит `VITE_BASE` (остаётся `/`) и оставляет `VITE_API_BASE` пустым — прокси Vite на `:8000` как раньше.

### 2. Включить GitHub Pages

В репозитории: **Settings → Pages → Source = GitHub Actions** (не branch / `docs`).

### 3. Переменная API

**Settings → Secrets and variables → Actions → Variables** (не Secrets):

| Name | Value |
|------|--------|
| `VITE_API_BASE` | `https://<future-amvera-host>` (публичный HTTPS origin бэкенда, **без** завершающего `/`) |

Это origin API, не секрет. Пока переменная пустая, Actions всё равно соберёт фронт; Mini App auth (`POST ${VITE_API_BASE}/api/auth/telegram`) будет падать — это ожидаемо, пока нет публичного HTTPS бэкенда.

`BOT_TOKEN`, ключи WB и прочие секреты **не** класть во фронт и не в Variables фронтенда. `BOT_TOKEN` только на сервере.

### 4. После commit + push

1. Actions: workflow **Deploy GitHub Pages** должен стать зелёным.
2. Mini App: **https://voronov20021-ux.github.io/selleros/**

### 5. BotFather (HTTPS обязателен)

В [@BotFather](https://t.me/BotFather):

1. `/mybots` → ваш бот → **Bot Settings → Menu Button → Configure**
2. **Web App URL** = `https://voronov20021-ux.github.io/selleros/`
3. Если есть **Mini App / Direct Link / Configure Mini App** — тот же URL.
4. URL должен быть **HTTPS**. Hash в BotFather не нужен: Telegram откроет корень, React подхватит `/#/`.

Telegram открывает Mini App из бота, читает `Telegram.WebApp.initData` (скрипт `https://telegram.org/js/telegram-web-app.js` в `index.html`) и клиент делает `POST { initData }` на `${VITE_API_BASE}/api/auth/telegram`.

Пока бэкенд не в публичном HTTPS, **UI грузится, логин/API падают** — так и задумано.

### 6. CORS (позже, не сейчас)

Когда Mini App на Pages, а API на Amvera, бэкенд должен разрешить origin **`https://voronov20021-ux.github.io`** (у CORS нет path `/selleros`). Auth-код бэкенда для деплоя Pages не менялся.

### 7. Секреты

Во фронтенде нет `BOT_TOKEN`, ключей WB API и `.env` секретов. В бандл попадает только публичный `VITE_API_BASE`.
