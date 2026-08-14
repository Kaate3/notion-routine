#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор структури місяця в картці рутини Notion.

Робить те саме, що кнопка «Операційні задачі | Створити», але з ПОРАХОВАНИМИ датами:
  📍 Поточні завдання | 01.08 → 31.08.2026
     ▸ Тиждень 1 | 01.08 → 09.08      ← межі рахуються з календаря
        ▸ Команда              (статуси задач / блокери / дедлайни)
        ▸ Перевірка оплат / документів
        ▸ Моніторинг та звітність   → ☐ 07.08 щотижневий звіт  ← пʼятниця тижня
        ▸ Комунікація
     ▸ Тиждень 2 | 10.08 → 16.08 …
     ▸ Inbox / brain dump          ← місячний рівень, поза тижнями

Правила нарізки тижнів:
  • тиждень = пн–нд, обрізаний межами місяця;
  • якщо перший або останній огризок коротший за 4 дні — він приклеюється до сусіднього
    (тому серпень 2026 = 4 тижні, а не 6).

Запуск:
  • по кнопці в Notion  → властивість-прапорець → вебхук → repository_dispatch
  • або по крону 1-го числа о 09:00 за Києвом
"""

import os
import sys
import calendar
import datetime as dt

import requests

# ---------------------------------------------------------------- конфіг карток

# Для кожної картки рутини — свій набір категорій.
# "items"          — рекурентні пункти, що зʼявляються в КОЖНОМУ тижні
# "weekly_report"  — додати «☐ <пʼятниця> щотижневий звіт»
# "month_level"    — категорії, що НЕ бʼються по тижнях (rolling)

CARDS = {
    # Operations HQ — Катя
    "3613647a-16dc-80cb-8c8b-d1d8f54ed191": {
        "title": "📍 Поточні завдання",
        "categories": [
            {"name": "Команда", "items": ["статуси задач", "блокери", "дедлайни"]},
            {"name": "Перевірка оплат / документів", "items": []},
            {"name": "Моніторинг та звітність", "items": [], "weekly_report": True},
            {"name": "Комунікація", "items": [
                "Участь у робочих зустрічах команди проєкту",
                "Координація з менторами та регіональними представниками",
            ]},
        ],
        "month_level": [{"name": "Inbox / brain dump", "items": []}],
    },

    # НАСТАВНИЦТВО | Operations — Мія
    "3433647a-16dc-80b5-836d-de09460c7bc2": {
        "title": "📍 Підбір та запуск пар",
        "categories": [
            {"name": "👥 Підбір і запуск нових наставницьких пар", "items": []},
            {"name": "🤝 Супровід і підтримка діючих пар", "items": []},
            {"name": "🔎 Залучення та відбір нових наставників", "items": []},
            {"name": "🛠️ Розвиток і вдосконалення системи", "items": []},
            {"name": "Система та менеджмент", "items": [], "weekly_report": True},
        ],
        "month_level": [],
    },
}

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_VERSION = "2022-06-28"
UA_MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
             "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]

S = requests.Session()
S.headers.update({
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
})


def notion(method: str, path: str, **kw) -> dict:
    r = S.request(method, f"https://api.notion.com/v1{path}", timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Notion {method} {path} → {r.status_code}: {r.text[:400]}")
    return r.json() if r.text else {}


def log(m: str) -> None:
    print(m, flush=True)


# ---------------------------------------------------------------- календар

def month_weeks(year: int, month: int) -> list[tuple[dt.date, dt.date]]:
    """Нарізка місяця на тижні пн–нд з приклеюванням коротких огризків."""
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])

    spans, cur = [], first
    while cur <= last:
        end = min(cur + dt.timedelta(days=6 - cur.weekday()), last)  # до неділі
        spans.append((cur, end))
        cur = end + dt.timedelta(days=1)

    def length(s):
        return (s[1] - s[0]).days + 1

    if len(spans) > 1 and length(spans[0]) < 4:
        spans[1] = (spans[0][0], spans[1][1])
        spans.pop(0)
    if len(spans) > 1 and length(spans[-1]) < 4:
        spans[-2] = (spans[-2][0], spans[-1][1])
        spans.pop()

    return spans


def friday_of(start: dt.date, end: dt.date) -> dt.date:
    """Пʼятниця всередині тижня — день щотижневого звіту."""
    d = end
    while d >= start:
        if d.weekday() == 4:
            return d
        d -= dt.timedelta(days=1)
    return end


# ---------------------------------------------------------------- блоки

def txt(content: str, bold: bool = False, color: str = "default") -> dict:
    return {"type": "text", "text": {"content": content},
            "annotations": {"bold": bold, "color": color}}


def date_mention(start: dt.date, end: dt.date | None = None) -> dict:
    d = {"start": start.isoformat()}
    if end and end != start:
        d["end"] = end.isoformat()
    return {"type": "mention", "mention": {"type": "date", "date": d}}


def todo(rich: list) -> dict:
    return {"object": "block", "type": "to_do",
            "to_do": {"rich_text": rich, "checked": False}}


def cat_block(cat: dict, span: tuple[dt.date, dt.date] | None) -> dict:
    """Категорія = згорнутий заголовок 3 рівня зі своїми чекбоксами."""
    kids = [todo([txt(i)]) for i in cat.get("items", [])]

    if cat.get("weekly_report") and span:
        fri = friday_of(*span)
        kids.append(todo([date_mention(fri), txt("  щотижневий звіт")]))

    if not kids:                      # порожня категорія — один порожній чекбокс
        kids = [todo([txt("")])]

    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [txt(cat["name"], bold=True)],
                          "is_toggleable": True, "color": "default", "children": kids}}


def week_block(idx: int, span: tuple[dt.date, dt.date], cfg: dict) -> dict:
    start, end = span
    color = "purple_background" if idx == current_week_index else "default"
    return {
        "object": "block", "type": "toggle",
        "toggle": {
            "rich_text": [txt(f"Тиждень {idx + 1}", bold=True), txt(" | "),
                          date_mention(start, end)],
            "color": color,
            "children": [cat_block(c, span) for c in cfg["categories"]],
        },
    }


def build(cfg: dict, year: int, month: int) -> list[dict]:
    weeks = month_weeks(year, month)
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])

    blocks = [{
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [
            txt(f"{cfg['title']} | ", bold=True),
            date_mention(first, last),
        ]},
    }]
    blocks += [week_block(i, w, cfg) for i, w in enumerate(weeks)]
    blocks += [cat_block(c, None) for c in cfg.get("month_level", [])]
    return blocks, weeks


# ---------------------------------------------------------------- вставка

def anchor_before_tasks(page_id: str) -> str | None:
    """
    Знаходить блок, ПІСЛЯ якого треба вставити структуру:
    останній блок перед поточним заголовком «📍 …».
    Якщо заголовка нема — повертає None (тоді дописуємо в кінець).
    """
    kids = notion("GET", f"/blocks/{page_id}/children", params={"page_size": 100})["results"]
    for i, b in enumerate(kids):
        if b["type"] == "heading_2":
            plain = "".join(t.get("plain_text", "") for t in b["heading_2"]["rich_text"])
            if "📍" in plain:
                return kids[i - 1]["id"] if i > 0 else None
    return None


def insert(page_id: str, blocks: list[dict]) -> None:
    body = {"children": blocks}
    after = anchor_before_tasks(page_id)
    if after:
        body["after"] = after
    notion("PATCH", f"/blocks/{page_id}/children", json=body)


# ---------------------------------------------------------------- main

def main() -> None:
    global current_week_index

    today = dt.date.today()
    year = int(os.environ.get("YEAR") or today.year)
    month = int(os.environ.get("MONTH") or today.month)

    only = os.environ.get("PAGE_ID", "").strip()
    targets = {only: CARDS[only]} if only and only in CARDS else CARDS

    for page_id, cfg in targets.items():
        weeks = month_weeks(year, month)
        current_week_index = next(
            (i for i, (s, e) in enumerate(weeks) if s <= today <= e), -1)

        blocks, weeks = build(cfg, year, month)
        insert(page_id, blocks)

        log(f"✔ {page_id}: {UA_MONTHS[month - 1]} {year} — {len(weeks)} тижнів")
        for i, (s, e) in enumerate(weeks):
            log(f"   Тиждень {i + 1}: {s:%d.%m} – {e:%d.%m}  (звіт {friday_of(s, e):%d.%m})")


if __name__ == "__main__":
    current_week_index = -1
    try:
        main()
    except Exception as e:
        log(f"✖ {e}")
        sys.exit(1)
