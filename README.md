# SkinVault — CS2 Portfolio Tracker (Telegram Mini App)

Трекер портфолио CS2 скинов с реальными ценами Steam Market, фото скинов, P&L и историей цен.

---

## Структура проекта

```
skinvault/
├── backend/
│   ├── main.py          # FastAPI API + раздача фронтенда
│   ├── bot.py           # Telegram бот
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   └── index.html       # Telegram Mini App (один файл)
├── docker-compose.yml
└── README.md
```

---

## Быстрый старт

### 1. Установить зависимости

```bash
cd backend
pip install -r requirements.txt
```

### 2. Запустить сервер

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Откройте `http://localhost:8000` — увидите Mini App.

---

## Настройка Telegram бота

### 1. Создать бота через @BotFather

```
/newbot → SkinVault → @yourskinvaultbot
```

Сохраните токен.

### 2. Сделать бота Mini App

```
/newapp → выбрать бота → URL вашего сервера (https://yourdomain.com)
```

### 3. Запустить бота

```bash
export BOT_TOKEN="your_token_here"
export WEBAPP_URL="https://yourdomain.com"
pip install python-telegram-bot
python bot.py
```

---

## Деплой (Railway / Render)

### Railway (рекомендуется, бесплатно)

1. Зарегистрируйтесь на railway.app
2. New Project → Deploy from GitHub
3. В Environment Variables добавьте:
   - `BOT_TOKEN` = токен из BotFather
   - `WEBAPP_URL` = ваш Railway URL (будет после деплоя)
4. В Settings → Networking → Generate Domain

### VPS (самый надёжный вариант)

```bash
# На сервере (Ubuntu)
git clone <ваш_репозиторий>
cd skinvault

# Запуск через Docker
docker-compose up -d

# Или напрямую
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000

# Nginx конфиг (для HTTPS — нужен для Telegram WebApp!)
# /etc/nginx/sites-available/skinvault
server {
    listen 443 ssl;
    server_name yourdomain.com;
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

> ⚠️ Telegram Mini App требует HTTPS. Локальный `localhost` работает только при тестировании через `@BotFather → Bot Settings → Menu Button → Test WebApp`.

---

## API эндпоинты

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/api/health` | Проверка сервера |
| GET | `/api/search?q=AK-47` | Поиск скинов в Steam Market |
| GET | `/api/price/{name}` | Текущая цена + URL фото |
| GET | `/api/portfolio/{steam_id}` | Портфолио с P&L |
| POST | `/api/portfolio/item` | Добавить предмет |
| PUT | `/api/portfolio/{steam_id}/{name}` | Обновить цену/количество |
| DELETE | `/api/portfolio/{steam_id}/{name}` | Удалить предмет |
| GET | `/api/history/{name}` | История цен |
| GET | `/api/inventory/{steam_id}` | Инвентарь Steam (публичный) |

### Пример: добавить предмет

```bash
curl -X POST http://localhost:8000/api/portfolio/item \
  -H "Content-Type: application/json" \
  -d '{"steam_id":"76561198000000000","market_hash_name":"AK-47 | Redline (Field-Tested)","buy_price":18.50,"quantity":1}'
```

---

## Как получить Steam64 ID

1. Открыть профиль Steam
2. Скопировать URL: `https://steamcommunity.com/profiles/76561198XXXXXXXXX`
3. Число после `/profiles/` — это Steam64 ID

Или использовать steamid.io

---

## Цены и лимиты

- Steam Market API: бесплатно, ~20 запросов/мин
- Цены кешируются в SQLite на 1 час
- История цен пишется при каждом обновлении кеша
- Изображения: официальный Steam CDN (akamai.steamstatic.com)

---

## Технологии

- **Backend**: Python 3.12, FastAPI, httpx, SQLite
- **Frontend**: Vanilla JS, Telegram WebApp SDK
- **Images**: Steam Community CDN
- **Prices**: Steam Market Price Overview API
- **Deploy**: Docker, Railway, Nginx
