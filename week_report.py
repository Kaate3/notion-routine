#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор тижневого міні-звіту в картці рутини Notion.

Логіка:
1. Знаходить картки рутини, де стоїть галочка «Згенерувати звіт»
   (або отримує конкретний page_id з repository_dispatch).
2. У тілі картки шукає тогл-блок тижня — той, у заголовку якого є
   дата-діапазон, що покриває сьогодні (з фолбеком на найближчий).
3. Рекурсивно збирає всі to-do з цього тижня по категоріях (виконані/невиконані).
4. Віддає структуровану вижимку в Gemini → отримує JSON зі звітом.
5. Вставляє тогл «🧾 Міні-звіт за тиждень …» всередину тижня (старий видаляє).
6. Знімає галочку.

Токени економляться так само, як у Task-Manager-Audit-Bot: уся аналітика
(підрахунки, групування, %) рахується в Python, в AI летить тільки вижимка.
"""

import os
import sys
import json
import datetime as dt
from typing import Any

import requests

# ---------------------------------------------------------------- налаштування

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# ID бази «Завдання» (collection / data source)
TASKS_DB = os.environ.get("NOTION_TASKS_DB", "3333647a-16dc-80fa-b9c6-000b79e8561a")

FLAG_PROP = os.environ.get("FLAG_PROP", "Згенерувати звіт")   # checkbox
TYPE_PROP = os.environ.get("TYPE_PROP", "Тип")                # select
TYPE_VALUE = os.environ.get("TYPE_VALUE", "🔄 Routine")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

NOTION_VERSION = "2025-09-03"
REPORT_MARKER = "🧾 Міні-звіт за тиждень"
TODAY = dt.date.today()

S = requests.Session()
S.headers.update({
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
})


def notion(method: str, path: str, **kw) -> dict:
    r = S.request(method, f"https://api.notion.com/v1{path}", timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Notion {method} {path} → {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else {}


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- читання Notion

def rt_text(rich_text: list) -> str:
    """Плоский текст з rich_text, включно з mention-датами."""
    out = []
    for t in rich_text:
        if t["type"] == "mention" and t["mention"]["type"] == "date":
            d = t["mention"]["date"]
            out.append(d["start"] + (f"→{d['end']}" if d.get("end") else ""))
        else:
            out.append(t.get("plain_text", ""))
    return "".join(out).strip()


def date_range(rich_text: list) -> tuple[dt.date, dt.date] | None:
    """Витягує перший дата-діапазон із заголовка блоку."""
    for t in rich_text:
        if t["type"] == "mention" and t["mention"]["type"] == "date":
            d = t["mention"]["date"]
            start = dt.date.fromisoformat(d["start"][:10])
            end = dt.date.fromisoformat(d["end"][:10]) if d.get("end") else start
            return start, end
    return None


def children(block_id: str) -> list[dict]:
    items, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        data = notion("GET", f"/blocks/{block_id}/children", params=params)
        items += data["results"]
        if not data.get("has_more"):
            return items
        cursor = data["next_cursor"]


def flagged_pages() -> list[str]:
    """Картки з увімкненою галочкою «Згенерувати звіт»."""
    body = {
        "filter": {
            "and": [
                {"property": FLAG_PROP, "checkbox": {"equals": True}},
                {"property": TYPE_PROP, "select": {"equals": TYPE_VALUE}},
            ]
        }
    }
    data = notion("POST", f"/data_sources/{TASKS_DB}/query", json=body)
    return [p["id"] for p in data["results"]]


def pick_week_block(page_id: str) -> dict | None:
    """
    Обирає тогл тижня: спершу той, що покриває сьогодні;
    якщо такого нема — останній завершений; якщо й такого нема — найближчий майбутній.
    """
    weeks = []
    for b in children(page_id):
        if b["type"] != "toggle":
            continue
        rng = date_range(b["toggle"]["rich_text"])
        if rng:
            weeks.append({"block": b, "start": rng[0], "end": rng[1]})

    if not weeks:
        return None

    current = [w for w in weeks if w["start"] <= TODAY <= w["end"]]
    if current:
        return current[0]

    past = sorted([w for w in weeks if w["end"] < TODAY], key=lambda w: w["end"])
    if past:
        return past[-1]

    return sorted(weeks, key=lambda w: w["start"])[0]


def collect_tasks(week_block_id: str) -> list[dict]:
    """
    Категорії тижня = прямі діти-тогли / заголовки.
    Усередині кожної рекурсивно збираються всі to-do.
    """
    categories = []

    def walk(block_id: str, bucket: list) -> None:
        for b in children(block_id):
            t = b["type"]
            if t == "to_do":
                text = rt_text(b["to_do"]["rich_text"])
                if text:
                    bucket.append({"text": text, "done": b["to_do"]["checked"]})
            if b.get("has_children"):
                walk(b["id"], bucket)

    loose: list = []
    for b in children(week_block_id):
        t = b["type"]
        title = rt_text(b[t]["rich_text"]) if b[t].get("rich_text") else ""

        if title.startswith(REPORT_MARKER):
            continue

        if t in ("toggle", "heading_1", "heading_2", "heading_3") and b.get("has_children"):
            bucket: list = []
            walk(b["id"], bucket)
            if bucket:
                categories.append({"title": title or "Без категорії", "items": bucket})
        elif t == "to_do":
            text = rt_text(b["to_do"]["rich_text"])
            if text:
                loose.append({"text": text, "done": b["to_do"]["checked"]})

    if loose:
        categories.append({"title": "Інше", "items": loose})

    return categories


# ---------------------------------------------------------------- AI

PROMPT = """Ти — операційний асистент українського благодійного фонду.
Нижче — фактичні дані з картки рутини співробітника за один робочий тиждень:
категорії напряму та чеклісти (виконано / не виконано).

Склади короткий тижневий міні-звіт українською мовою, який людина зможе
скопіювати у щотижневий та щомісячний звіт фонду.

Правила:
- Пиши тільки на основі наданих пунктів. Нічого не вигадуй і не додавай метрик, яких немає.
- Формулюй результатами, а не переліком дій: «проведено рев'ю 2 пар», а не «зробити рев'ю».
- Не копіюй пункти дослівно — узагальнюй споріднені в одне речення.
- Тон діловий, без води, без вступів і без емодзі в тексті.
- Якщо в якомусь блоці даних нема — поверни порожній масив, не вигадуй.

Поверни ЛИШЕ JSON такої структури, без markdown-обгортки:
{
  "summary": "2-3 речення про ключове за тиждень",
  "done": ["результат 1", "результат 2"],
  "in_progress": ["що в роботі / переноситься"],
  "risks": ["ризики, блокери, що не вдалося і чому"],
  "next": ["фокус на наступний тиждень"]
}

ДАНІ ТИЖНЯ ({period}):
{data}
"""


def build_digest(categories: list[dict]) -> tuple[str, dict]:
    lines, stats = [], {"total": 0, "done": 0}
    for c in categories:
        done = [i["text"] for i in c["items"] if i["done"]]
        todo = [i["text"] for i in c["items"] if not i["done"]]
        stats["total"] += len(c["items"])
        stats["done"] += len(done)
        lines.append(f"\n## {c['title']}")
        lines.append("Виконано: " + ("; ".join(done) if done else "—"))
        lines.append("Не виконано: " + ("; ".join(todo) if todo else "—"))
    stats["pct"] = round(stats["done"] / stats["total"] * 100) if stats["total"] else 0
    return "\n".join(lines), stats


def gemini(prompt: str) -> dict:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "response_mime_type": "application/json"},
    }
    r = requests.post(url, json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:500]}")
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text.replace("```json", "").replace("```", "").strip())


# ---------------------------------------------------------------- запис у Notion

def para(text: str, bold: bool = False) -> dict:
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{
            "type": "text", "text": {"content": text},
            "annotations": {"bold": bold},
        }]},
    }


def bullet(text: str) -> dict:
    return {
        "object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def render(report: dict, period: str, stats: dict) -> dict:
    kids = [para(report.get("summary", "").strip())]
    sections = [
        ("Виконано", report.get("done")),
        ("У процесі / переноситься", report.get("in_progress")),
        ("Ризики та блокери", report.get("risks")),
        ("Фокус на наступний тиждень", report.get("next")),
    ]
    for title, items in sections:
        if not items:
            continue
        kids.append(para(title, bold=True))
        kids += [bullet(str(i)) for i in items]

    kids.append(para(
        f"Прогрес чеклісту: {stats['done']}/{stats['total']} ({stats['pct']}%) · "
        f"згенеровано {TODAY.strftime('%d.%m.%Y')}"
    ))

    return {
        "object": "block", "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": f"{REPORT_MARKER} {period}"}}],
            "color": "purple_background",
            "children": kids,
        },
    }


def drop_old_report(week_block_id: str) -> None:
    for b in children(week_block_id):
        if b["type"] == "toggle" and rt_text(b["toggle"]["rich_text"]).startswith(REPORT_MARKER):
            notion("DELETE", f"/blocks/{b['id']}")
            log("   старий звіт видалено")


def set_flag(page_id: str, value: bool) -> None:
    notion("PATCH", f"/pages/{page_id}",
           json={"properties": {FLAG_PROP: {"checkbox": value}}})


# ---------------------------------------------------------------- основний цикл

def process(page_id: str) -> None:
    log(f"→ картка {page_id}")

    week = pick_week_block(page_id)
    if not week:
        log("   ✖ не знайдено жодного тогла тижня з дата-діапазоном")
        set_flag(page_id, False)
        return

    period = f"{week['start'].strftime('%d.%m')}–{week['end'].strftime('%d.%m.%Y')}"
    log(f"   тиждень: {period}")

    categories = collect_tasks(week["block"]["id"])
    if not categories:
        log("   ✖ у тижні порожньо, звіт не потрібен")
        set_flag(page_id, False)
        return

    data, stats = build_digest(categories)
    log(f"   зібрано {stats['total']} пунктів, виконано {stats['done']}")

    report = gemini(PROMPT.replace("{period}", period).replace("{data}", data))

    drop_old_report(week["block"]["id"])
    notion("PATCH", f"/blocks/{week['block']['id']}/children",
           json={"children": [render(report, period, stats)]})
    set_flag(page_id, False)
    log("   ✔ звіт вставлено")


def main() -> None:
    payload_page = os.environ.get("PAGE_ID", "").strip()
    pages = [payload_page] if payload_page else flagged_pages()

    if not pages:
        log("Немає карток із запитом на звіт.")
        return

    errors = 0
    for pid in pages:
        try:
            process(pid)
        except Exception as e:  # одна зламана картка не валить решту
            errors += 1
            log(f"   ✖ помилка: {e}")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
