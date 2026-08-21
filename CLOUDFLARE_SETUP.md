# Деплой Cloudflare Worker (заміна Pipedream)

Одноразове налаштування, повністю безкоштовно (free tier Cloudflare Workers: 100 000 запитів/день).

## 1. Створи GitHub-токен для Worker'а

Це той самий крок, що й раніше, тільки токен тепер піде НЕ в публічний HTML, а в приховане сховище Cloudflare:

1. https://github.com/settings/personal-access-tokens/new
2. Repository access → Only select repositories → `Kaate3/notion-routine`
3. Permissions → Actions → **Read and write**
4. Generate token, скопіюй значення (`github_pat_...`)

## 2. Встанови Wrangler (CLI Cloudflare)

```bash
npm install -g wrangler
```

## 3. Увійди в Cloudflare (створить безкоштовний акаунт, якщо його ще нема)

```bash
wrangler login
```

Відкриється браузер — увійди/зареєструйся, підтверди доступ.

## 4. Задеплой Worker

```bash
cd cloudflare-worker
wrangler secret put GITHUB_TOKEN
# встав токен із кроку 1, натисни Enter

wrangler deploy
```

В кінці `wrangler deploy` виведе URL типу:

```
https://notion-routine-dispatch.<твій-субдомен>.workers.dev
```

## 5. Встав URL у index.html

У кореневому `index.html` заміни рядок:

```js
const WEBHOOK_URL = "https://ВСТАВ_СЮДИ_URL_ВОРКЕРА.workers.dev";
```

на реальний URL з кроку 4, закомить і запуш.

Готово — кнопки в Notion відтепер б'ють у твій власний Worker, який ховає токен і безкоштовно форвардить запит у GitHub Actions.
