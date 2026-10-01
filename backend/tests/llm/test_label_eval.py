"""Оценка распознавания на настоящих этикетках — платно и только вручную.

Запуск: ``LABEL_EVAL_DIR=/путь/к/папке make eval`` (нужен ``POLZA_API_KEY``).
В PR и в обычный ``pytest`` не входит: маркер ``llm`` исключён по умолчанию.

Фото лежат вне репозитория: на этикетках — названия поставщиков и
изготовителей. В папке ``LABEL_EVAL_DIR`` рядом с каждым ``имя.jpg`` лежит
``имя.json`` — что должно получиться в карточке, полями домена::

    {"label_name": "Сыр «Гауда» 45%", "protein": "25", "kcal": "343",
     "shelf_life_sealed": "12 месяцев (с 15.06.2025 до 15.06.2026) при t -18°C"}

Проверяются только поля, названные в файле; ``null`` — поле должно остаться
пустым. Сравнивается итог целиком — прочтение моделью и разбор кодом:
повару важно, что окажется в карточке. Нет папки, фото или ключа — тест
пропускается, а не молча зеленеет.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from kitchen.config import load_settings
from kitchen.domain.cards import label_fields_from_extraction
from kitchen.llm.budget import UNKNOWN_COST_RUB
from kitchen.llm.label import LabelReader, label_reader_from_settings

pytestmark = pytest.mark.llm


def _photos() -> list[Path]:
    folder = os.environ.get("LABEL_EVAL_DIR", "")
    if not folder:
        return []
    return sorted(
        photo for photo in Path(folder).glob("*.jpg") if photo.with_suffix(".json").is_file()
    )


_NO_PHOTOS = pytest.param(
    None,
    marks=pytest.mark.skip(reason="нет фото этикеток: задайте LABEL_EVAL_DIR (см. докстринг)"),
    id="нет-фото",
)


@pytest.fixture(scope="module")
def reader() -> LabelReader:
    found = label_reader_from_settings(load_settings())
    if found is None:
        pytest.skip("не задан POLZA_API_KEY — оценка распознавания пропущена")
    return found


@pytest.mark.parametrize("photo", _photos() or [_NO_PHOTOS], ids=lambda p: getattr(p, "stem", p))
def test_label_is_read_as_expected(reader: LabelReader, photo: Path) -> None:
    expected: dict[str, str | None] = json.loads(
        photo.with_suffix(".json").read_text(encoding="utf-8")
    )

    reading = reader.read(photo.read_bytes())
    fields, warnings = label_fields_from_extraction(reading.extraction.model_dump())

    cost = reading.reply.cost_rub
    print(
        f"{photo.name}: {reading.reply.duration_ms} мс, "
        f"{cost if cost is not None else f'≈{UNKNOWN_COST_RUB}'} ₽; замечания: {warnings}"
    )
    wrong = [
        f"{name}: ждали {want!r}, получили {fields.get(name)!r}"
        for name, want in expected.items()
        if not _same(fields.get(name), want)
    ]
    assert not wrong, "\n".join(wrong)


def _same(got: object, want: str | None) -> bool:
    if want is None:
        return got in (None, "")
    if isinstance(got, Decimal):
        return got == Decimal(want)
    return got == want
