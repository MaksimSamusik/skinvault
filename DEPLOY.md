# Deploy на Railway

Краткая инструкция для production-деплоя SkinVault на Railway.

## 1. Подготовка репозитория

Закоммить текущее состояние и запушь в GitHub:

```bash
git add -A
git commit -m "Prepare for Railway deploy"
git push origin master
```

## 2. Создание проекта на Railway

1. Зарегистрируйся на [railway.com](https://railway.com).
2. **New Project** → **Deploy from GitHub repo** → выбери свой репозиторий `skinvault`.
3. Railway автоматически:
   - Определит Python через `runtime.txt` (3.12.3).
   - Использует `nixpacks` (см. `railway.toml`).
   - Установит зависимости из `requirements.txt`.
   - Запустит сервер по startCommand из `railway.toml`.

## 3. Добавление PostgreSQL

В дашборде проекта:

1. **+ New** → **Database** → **Add PostgreSQL**.
2. Railway автоматически добавит env var `DATABASE_URL` в твой web-сервис.
3. Код сам обработает schema URL: `postgres://` → `postgresql+asyncpg://`.
4. При первом запуске сработает миграция (`init_db` + `_migrate_postgres_portfolio_id`).

## 4. Environment variables

В **Variables** твоего web-сервиса добавь:

| Variable             | Зачем                                                              |
|----------------------|--------------------------------------------------------------------|
| `STEAM_API_KEY`      | (Опц.) Ускоряет resolve vanity URL. Получить: [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) |
| `CSGOMARKET_API_KEY` | (Опц.) Включает третий источник цен                                |
| `BOT_TOKEN`          | (Опц.) Только если будешь запускать `bot.py` отдельным сервисом    |
| `WEBAPP_URL`         | (Опц.) URL прода для Telegram бота                                 |
| `ADMIN_TOKEN`        | Включает `/api/admin/*` endpoints. Сгенерируй: `openssl rand -hex 32` |

`DATABASE_URL` ставит сама Railway, не трогай.
`STEAM_OPENID_RETURN` оставь пустым — определится автоматически из proxy headers.

## 5. Public domain

В **Settings** → **Networking** → **Generate Domain**.

Получишь URL вида `https://skinvault-production-xxxx.up.railway.app`. HTTPS уже настроен.

## 6. Telegram Mini App

1. У `@BotFather`: `/newbot` или существующий бот.
2. `/newapp` → выбери бота → `Web App URL` = твой Railway URL.
3. Готово, Mini App открывается в Telegram.

## 7. Первый запуск

Зайди на `https://<your-domain>/api/health` — должно вернуть `{"status":"ok","db_type":"postgres",...}`.

Lisskins-цены качаются в фоне при старте (840MB, ~60 сек). Следи в **Deployments** → **Logs**:

```
[lisskins] загружено 0 цен (возраст файла: ...)
[lisskins] кэш старше 60 мин, запускаем фоновый refresh
[lisskins] refresh: качаем bulk JSON напрямую...
[lisskins] direct: скачано 840 MB
[lisskins] refresh: обновлено 22951 цен за 61.7s (direct)
```

## 8. Триггер refresh вручную

```bash
curl -X POST -H "X-Admin-Token: <your-token>" \
  "https://<your-domain>/api/admin/refresh-lisskins?wait=true"
```

## Подводные камни

### Память на free-плане

Railway free-trial = 512MB RAM. Парсинг 840MB JSON через `ijson` стримит, но в момент пика
может уходить в swap. Если OOM — апгрейдь на Hobby ($5/мес, 8GB) или сделай так чтобы
обновление парсило и сразу удаляло raw файл (уже сделано).

### Persistent volume

Railway по умолчанию **не сохраняет файловую систему между деплоями**. Файл
`database/lisskins_prices.json` будет перекачиваться при каждом деплое. Для bulk-refresh
это OK (1 минута), но если хочешь warm-start — используй [Railway Volume](https://docs.railway.com/reference/volumes):

1. **Settings** → **Volumes** → **+ Add**.
2. Mount path: `/app/database`.
3. После этого `lisskins_prices.json` сохраняется.

### Cold start

При деплое цены загружаются раз в час (`LISSKINS_REFRESH_INTERVAL`). При старте,
если файл старше часа — запускается refresh в фоне. До его окончания lisskins-цены
будут пустые / старые. Чтобы прогреть без ожидания — POST на `/api/admin/refresh-lisskins`.

### Playwright

Убран из `requirements.txt` — lis-skins.com сейчас отдаёт JSON напрямую без Cloudflare.
Если защита вернётся — раскомментируй `playwright>=1.40.0` в `requirements.txt`,
добавь buildpack для Chromium (или nixpacks с `nixPkgs = ["chromium"]`).
