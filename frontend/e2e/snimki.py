"""Снимки фронтенда на телефоне и компьютере с подменёнными ответами API.

Запуск (из backend/, где стоит Playwright):
    VIRTUAL_ENV= uv run python ../frontend/e2e/snimki.py            # соберёт и поднимет vite preview на 4173
    VIRTUAL_ENV= uv run python ../frontend/e2e/snimki.py --port 5173  # против уже запущенного dev-сервера

Dev-сервер для --port запускайте как `npm run dev -- --host 127.0.0.1`: без
--host vite на Windows слушает только ::1, и скрипт не достучится по 127.0.0.1.

Если backend/.venv нет (свежий worktree), Playwright берётся во временное
окружение той версии, что в uv.lock:
    VIRTUAL_ENV= uv run --no-project --with playwright==1.62.0 python ../frontend/e2e/snimki.py

Проверки: горизонтальное переполнение 0 на всех экранах; в блюдах на 360 px
не меньше 8 строк в первом экране; кнопки и ссылки в шапке, содержимом и
вкладках не ниже 44 px при is_mobile. Провал — код возврата 1.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ZDES = Path(__file__).resolve().parent
FRONTEND = ZDES.parent
OUT = ZDES / "snimki"
# В package.json нет скрипта preview, и запускать сервер через npm/npx не
# стоит: на Windows это цепочка cmd.exe → npm.cmd → node, и terminate()
# убил бы только cmd.exe — node остался бы жить и держать порт. Node
# напрямую — сам сервер и есть дочерний процесс.
VITE = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"

ME = {"email": "chef@example.com", "display_name": "Бренд-шеф", "roles": ["chef", "developer"]}
SYNC = {
    "data_as_of": "2026-09-30T14:05:00+00:00", "changed_at": "2026-09-30T13:50:00+00:00", "stale": False,
    "books": [
        {"book": "kitchen", "title": "таблица кухни", "checked_at": "2026-09-30T14:05:00+00:00",
         "changed_at": "2026-09-30T13:50:00+00:00", "stale": False, "problem": None, "problem_since": None},
        {"book": "ingredient_cards", "title": "карточки ингредиентов", "checked_at": "2026-09-30T14:05:00+00:00",
         "changed_at": None, "stale": False, "problem": None, "problem_since": None},
    ],
}
BLYUDA = [
    ("B001", "Круассан с ветчиной и сыром", "Выпечка", "280", "84.66", "30.2", "69.8", "160", 2),
    ("B002", "Чизбургер классический", "Бургеры", "320", "112.40", "35.1", "64.9", "210", 0),
    ("B003", "Салат Цезарь с курицей", "Салаты", "390", "141.05", "36.2", "63.8", "250", 1),
    ("B004", "Картофель фри большой", "Гарниры", "150", "38.90", "25.9", "74.1", "180", 0),
    ("B005", "Хот-дог датский с брусничным соусом", "Хот-доги", "220", "97.30", "44.2", "55.8", "190", 1),
    ("B099", "Комбо «Семейное» 4 бургера, 2 картофеля, 4 напитка", "Комбо", "280", "1398.00", "499.3", "-399.3", "2400", 3),
    ("B010", "Лимонад домашний", "Напитки", "140", "22.15", "15.8", "84.2", "400", 0),
    ("B011", "Суп-пюре тыквенный", "Супы", None, "61.00", None, None, "300", 1),
    ("B012", "Пицца Пепперони 30 см", "Пицца", "590", "168.44", "28.5", "71.5", "520", 0),
    ("B013", "Наггетсы 9 шт", "Закуски", "230", "78.12", "34.0", "66.0", "170", 0),
    ("B014", "Кофе капучино 300 мл", "Напитки", "180", "31.60", "17.6", "82.4", "300", 0),
    ("B015", "Ролл с лососем и авокадо", "Роллы", "410", "155.90", "38.0", "62.0", "230", 2),
]
DISHES = [
    {"legacy_id": i, "name": n, "category": c, "status": "активное", "price_menu": p, "uc_rub": uc,
     "uc_percent": ucp, "margin_percent": m, "output_grams": o, "warnings": w}
    for i, n, c, p, uc, ucp, m, o, w in BLYUDA
]
DETAIL = {
    **DISHES[0], "protein_g": "12.4", "fat_g": "18.9", "carbs_g": "31.0", "kcal": "344", "kbju_coverage": "0.97",
    "components": [
        {"name": "Круассан сливочный 60гр", "short_name": "Круассан", "row_type": "main", "unit": "шт",
         "net_weight_g": "1", "gross_weight_g": "1.00", "price_per_unit": "49.50", "cost_rub": "0.83", "share_percent": "1.0"},
        {"name": "Ветчина варёная Останкино", "short_name": "Ветчина", "row_type": "main", "unit": "кг",
         "net_weight_g": "40", "gross_weight_g": "42.11", "price_per_unit": "620.00", "cost_rub": "26.11", "share_percent": "30.8"},
        {"name": "Сыр Гауда 45%", "short_name": "", "row_type": "main", "unit": "кг",
         "net_weight_g": "30", "gross_weight_g": "31.58", "price_per_unit": "890.00", "cost_rub": "28.11", "share_percent": "33.2"},
        {"name": "Контейнер бумажный без крышки 207х127х55 крафт/чёрный", "short_name": "", "row_type": "packaging",
         "unit": "шт", "net_weight_g": "1", "gross_weight_g": None, "price_per_unit": "12.40", "cost_rub": "12.40", "share_percent": "14.6"},
    ],
    "warning_texts": ["Штучный «Круассан сливочный 60гр»: нетто 1 г при весе штуки 60 г — похоже, вписано количество штук, а не граммы; себестоимость занижена"],
}
INGREDIENTS = [
    {"id": int(i), "legacy_id": i, "name": n, "category": c, "unit": u, "status": s, "price_per_kg": p,
     "weight_per_piece_g": w, "has_card": h}
    for i, n, c, u, s, p, w, h in [
        ("1", "Круассан сливочный 60гр", "Выпечка", "шт", "активный", "49.50", "60", True),
        ("2", "Ветчина варёная Останкино", "Мясо", "кг", "активный", "620.00", None, True),
        ("3", "Сыр Гауда 45%", "Сыры", "кг", "активный", "890.00", None, False),
        ("5", "Контейнер бумажный без крышки 207х127х55 крафт/чёрный", "Упаковка", "шт", "активный", "12.40", None, False),
        ("6", "Свёкла", "Овощи", "кг", "архив", None, None, False),
        ("7", "Огурцы резаные", "Овощи", "кг", "активный", "180.00", None, True),
        ("8", "Котлета говяжья 100 г", "Мясо", "шт", "активный", "48.00", "100", True),
        ("9", "Булочка для бургера с кунжутом", "Выпечка", "шт", "активный", "9.80", "70", True),
        ("10", "Лосось слабосолёный", "Рыба", "кг", "активный", "1480.00", None, False),
        ("12", "Молоко 3,2%", "Молочные продукты", "л", "активный", "78.00", None, True),
    ]
]
RECON = {
    "total": 101, "linked": 85, "needs_human": 16,
    "rows": [
        {"card_id": 1, "name": "Соус барбекю РБК", "link_status": "ambiguous", "supplier": "Метро",
         "candidates": [{"ingredient_id": 21, "legacy_id": "21", "name": "Соус барбекю РБК", "score": None},
                        {"ingredient_id": 22, "legacy_id": "22", "name": "Соус барбекю РБК", "score": None}]},
        {"card_id": 2, "name": "Огурцы не резаные", "link_status": "candidate", "supplier": "Восток",
         "candidates": [{"ingredient_id": 7, "legacy_id": "7", "name": "Огурцы резаные", "score": None}]},
        {"card_id": 3, "name": "Тостовый хлеб", "link_status": "orphan", "supplier": "Хлебозавод", "candidates": []},
    ],
}


def otvet(route, request, state):
    path = request.url.split("/api", 1)[1].split("?")[0]
    def json_(telo, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(telo))
    if path == "/me":
        return json_(ME) if state["me"] == 200 else json_({"detail": "нужен токен"}, 401)
    if path == "/sync":
        return json_(state.get("sync", SYNC))
    if path == "/dishes":
        return json_(DISHES)
    if path.startswith("/dishes/"):
        return json_(DETAIL)
    if path == "/ingredients":
        return json_(INGREDIENTS)
    if path == "/reconciliation":
        return json_(RECON)
    if path == "/auth/refresh":
        return json_({"detail": "сессия"}, 401)
    return json_({"detail": "not found"}, 404)


def zhdat_port(port: int, server: subprocess.Popen | None = None, timeout: float = 90) -> None:
    konets = time.time() + timeout
    while time.time() < konets:
        # Сервер умер, не подняв порт (занят при --strictPort, нет dist/):
        # ждать дальше нечего, а чужой сервер на том же порту нам не нужен.
        if server is not None and server.poll() is not None:
            raise SystemExit(f"vite preview завершился с кодом {server.returncode}, порт {port} не поднят")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"сервер на {port} не поднялся")


def zavershit(server: subprocess.Popen) -> None:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # Windows: terminate() — TerminateProcess только для самого node;
        # если он не отпустил порт, снимаем всё дерево.
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], check=False)
        else:
            server.kill()
        server.wait(timeout=10)


class Proverki:
    def __init__(self) -> None:
        self.provaly: list[str] = []

    def snimok(self, page: Page, imya: str, *, full: bool = False, mobile: bool = False) -> None:
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / f"{imya}.png"), full_page=full)
        perepolnenie = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        print(f"{imya}: переполнение {perepolnenie}px")
        if perepolnenie > 0:
            self.provaly.append(f"{imya}: горизонтальное переполнение {perepolnenie}px")
        if mobile:
            melkie = page.evaluate(
                """() => [...document.querySelectorAll('header button, header a, main button, main a, nav a, [role=dialog] button, [role=dialog] a')]
                    .filter(el => el.offsetParent !== null || el.closest('[role=dialog]'))
                    .map(el => ({t: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40), r: el.getBoundingClientRect()}))
                    .filter(x => x.r.width > 0 && x.r.height > 0 && (x.r.height < 44 || x.r.width < 44))
                    .map(x => `${x.t} ${Math.round(x.r.width)}×${Math.round(x.r.height)}`)"""
            )
            if melkie:
                self.provaly.append(f"{imya}: цели меньше 44 px — {melkie[:6]}")

    def strok(self, page: Page, imya: str, minimum: int) -> None:
        # Строки, чей верх помещается в первый экран.
        n = page.evaluate("[...document.querySelectorAll('.stroka, .spisok > li')].filter(li => li.getBoundingClientRect().top < window.innerHeight).length")
        print(f"{imya}: строк в первом экране {n}")
        if n < minimum:
            self.provaly.append(f"{imya}: строк в первом экране {n}, нужно не меньше {minimum}")


def snyat(base: str) -> Proverki:
    OUT.mkdir(exist_ok=True)
    p = Proverki()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for shirina, vysota, prefix, mobile in ((360, 780, "tel360", True), (390, 844, "tel390", True), (1440, 900, "pk", False)):
            state = {"me": 401}
            ctx = browser.new_context(viewport={"width": shirina, "height": vysota}, device_scale_factor=2,
                                      is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()
            page.route(re.compile(rf"^{re.escape(base)}/api/"), lambda r, q: otvet(r, q, state))
            page.goto(f"{base}/dishes")
            page.wait_for_selector("form", timeout=15000)
            p.snimok(page, f"{prefix}-01-vhod", mobile=mobile)

            state["me"] = 200
            page.goto(f"{base}/dishes")
            page.wait_for_selector(".stroka, .spisok, .tablitsa", timeout=15000)
            p.snimok(page, f"{prefix}-02-blyuda", mobile=mobile)
            if mobile:
                p.strok(page, f"{prefix}-02-blyuda", 8)
                page.click("button[aria-label='Разделы']")
                page.wait_for_selector("[role=dialog]")
                p.snimok(page, f"{prefix}-03-shtorka", mobile=mobile)
                page.keyboard.press("Escape")
                knopka = page.locator("button", has_text=re.compile("^(Фильтры|Сортировка и фильтры)"))
                if knopka.count():
                    knopka.first.click()
                    page.wait_for_timeout(300)
                    p.snimok(page, f"{prefix}-04-otbor", mobile=mobile)
                    page.keyboard.press("Escape")

            page.goto(f"{base}/dishes/B001")
            page.wait_for_selector(".kartochka h1", timeout=15000)
            p.snimok(page, f"{prefix}-05-kartochka", full=True, mobile=mobile)

            page.goto(f"{base}/ingredients")
            page.wait_for_selector(".stroka, .spisok, .tablitsa", timeout=15000)
            p.snimok(page, f"{prefix}-06-spravochnik", mobile=mobile)

            page.goto(f"{base}/reconciliation")
            page.wait_for_selector("main", timeout=15000)
            page.wait_for_timeout(600)
            p.snimok(page, f"{prefix}-07-sverka", full=True, mobile=mobile)

            state["sync"] = {**SYNC, "stale": True, "books": [{**SYNC["books"][0], "stale": True, "problem": "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"}, SYNC["books"][1]]}
            page.goto(f"{base}/dishes")
            page.wait_for_selector(".svezhest--staro", timeout=15000)
            p.snimok(page, f"{prefix}-08-polosa", mobile=mobile)
            ctx.close()
        browser.close()
    return p


def main() -> int:
    # В трубе на Windows stdout и stderr идут в cp1251: «←» из «← Блюда» в
    # строке провала уронил бы печать, а текст SystemExit (он идёт в stderr)
    # выходил бы кракозябрами. Построчная буферизация — чтобы строки проверок
    # не перемешивались с выводом vite.
    for potok in (sys.stdout, sys.stderr):
        potok.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None, help="уже запущенный dev-сервер; без него — сборка и vite preview на 4173")
    args = parser.parse_args()
    server = None
    if args.port is None:
        subprocess.run(["npm", "run", "build"], cwd=FRONTEND, check=True, shell=sys.platform == "win32")
        # --host обязателен: без него vite слушает «localhost», а это на
        # Node 17+ под Windows только ::1 — и 127.0.0.1, по которому ходит
        # скрипт и по которому перехватывается /api/, молчит.
        server = subprocess.Popen(
            ["node", str(VITE), "preview", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], cwd=FRONTEND
        )
        port = 4173
    else:
        port = args.port
    try:
        zhdat_port(port, server)
        p = snyat(f"http://127.0.0.1:{port}")
    finally:
        if server is not None:
            zavershit(server)
    if p.provaly:
        print("ПРОВАЛЫ:")
        for stroka in p.provaly:
            print(" -", stroka)
        return 1
    print(f"Все проверки прошли, снимки в {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
