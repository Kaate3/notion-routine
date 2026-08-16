#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор структури місяця в картці рутини Notion.

ЗМІНА проти попередньої версії: раніше скрипт ІГНОРУВАВ чекбокс
«Створити місяць» і працював лише вручну / по кроду 1-го числа.
Тепер він:
  1. Опитує базу «Завдання» на картки з увімкненим FLAG_PROP
     («Створити місяць» = true) серед карток типу TYPE_VALUE.
  2. Будує структуру тільки для цих карток (або для PAGE_ID, якщо задано
     явно з repository_dispatch).
  3. Знімає чекбокс після успішної генерації — так само, як week_report.py.

Правила нарізки тижнів лишились ті самі:
  • тиждень = пн–нд, обрізаний межами місяця;
  • короткий (< 4 днів) огризок приклеюється до сусіднього тижня.
"""

import os
import sys
import calendar
import datetime as dt

import requests

# ---------------------------------------------------------------- конфіг карток
# Для кожної картки рутини — свій набір категорій.
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

# База «Завдання» — той самий data source, що і в week_report.py
TASKS_DB = os.environ.get("NOTION_TASKS_DB", "3333647a-16dc-80fa-b9c6-000b79e8561a")
FLAG_PROP = os.environ.get("MONTH_FLAG_PROP", "Створити місяць")   # checkbox
TYPE_PROP = os.environ.get("TYPE_PROP", "Тип")                     # select
TYPE_VALUE = os.environ.get("TYPE_VALUE", "🔄 Routine")

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


# ---------------------------------------------------------------- хто просить місяць

def flagged_pages() -> list[str]:
    """Картки з увімкненим чекбоксом «Створити місяць», серед 🔄 Routine."""
    body = {
        "filter": {
            "and": [
                {"property": FLAG_PROP, "checkbox": {"equals": True}},
                {"property": TYPE_PROP, "select": {"equals": TYPE_VALUE}},
            ]
        }
    }
    data = notion("POST", f"/databases/{TASKS_DB}/query", json=body)
    return [p["id"] for p in data["results"]]


def set_flag(page_id: str, value: bool) -> None:
    notion("PATCH", f"/pages/{page_id}",
           json={"properties": {FLAG_PROP: {"checkbox": value}}})


# ---------------------------------------------------------------- календар

def month_weeks(year: int, month: int) -> list[tuple[dt.date, dt.date]]:
    """Нарізка місяця на тижні пн–нд з приклеюванням коротких огризків."""
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])

    spans, cur = [], first
    while cur <= last:
        end = min(cur + dt.timedelta(days=6 - cur.weekday()), last)
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
    kids = [todo([txt(i)]) for i in cat.get("items", [])]

    if cat.get("weekly_report") and span:
        fri = friday_of(*span)
        kids.append(todo([date_mention(fri), txt("  щотижневий звіт")]))

    if not kids:
        kids = [todo([txt("")])]

    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [txt(cat["name"], bold=True)],
                          "is_toggleable": True, "color": "default", "children": kids}}


def week_block(idx: int, span: tuple[dt.date, dt.date], cfg: dict, current_idx: int) -> dict:
    start, end = span
    color = "purple_background" if idx == current_idx else "default"
    return {
        "object": "block", "type": "toggle",
        "toggle": {
            "rich_text": [txt(f"Тиждень {idx + 1}", bold=True), txt(" | "),
                          date_mention(start, end)],
            "color": color,
            "children": [cat_block(c, span) for c in cfg["categories"]],
        },
    }


def build(cfg: dict, year: int, month: int) -> tuple[list[dict], list]:
    weeks = month_weeks(year, month)
    today = dt.date.today()
    current_idx = next((i for i, (s, e) in enumerate(weeks) if s <= today <= e), -1)

    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])

    blocks = [{
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [
            txt(f"{cfg['title']} | ", bold=True),
            date_mention(first, last),
        ]},
    }]
    blocks += [week_block(i, w, cfg, current_idx) for i, w in enumerate(weeks)]
    blocks += [cat_block(c, None) for c in cfg.get("month_level", [])]
    return blocks, weeks


# ---------------------------------------------------------------- вставка

def anchor_before_tasks(page_id: str) -> str | None:
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

def process(page_id: str, year: int, month: int) -> None:
    cfg = CARDS.get(page_id)
    if not cfg:
        log(f"   ✖ картка {page_id} не описана в CARDS — пропускаю")
        set_flag(page_id, False)
        return

    blocks, weeks = build(cfg, year, month)
    insert(page_id, blocks)
    set_flag(page_id, False)

    log(f"✔ {page_id}: {UA_MONTHS[month - 1]} {year} — {len(weeks)} тижнів")
    for i, (s, e) in enumerate(weeks):
        log(f"   Тиждень {i + 1}: {s:%d.%m} – {e:%d.%m}  (звіт {friday_of(s, e):%d.%m})")


def main() -> None:
    today = dt.date.today()
    year = int(os.environ.get("YEAR") or today.year)
    month = int(os.environ.get("MONTH") or today.month)

    # 1. Явний page_id (ручний запуск / repository_dispatch)
    forced = os.environ.get("PAGE_ID", "").strip()
    if forced:
        targets = [forced]
    else:
        # 2. Інакше — питаємо Notion, у кого зараз стоїть галочка
        targets = flagged_pages()

    if not targets:
        log("Немає карток із запитом на створення місяця.")
        return

    errors = 0
    for page_id in targets:
        try:
            process(page_id, year, month)
        except Exception as e:
            errors += 1
            log(f"   ✖ помилка на {page_id}: {e}")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
