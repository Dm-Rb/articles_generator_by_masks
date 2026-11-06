"""Модуль содержит функционал извлечения масок артикулов из текста"""

import asyncio
import re


class MaskExtractor:
    """
    Класс для извлечения масок артикулов из текста регулярными выражениями.

    Результат имеет ту же структуру, что и ответ ИИ-агента:
    [{"manufacture": None, "group": None, "symbol": "*", "masks": ["4VB****", ...]}]

    Регулярные выражения не определяют производителя и товарную группу,
    поэтому эти поля всегда None: пользователь определяет их вручную.
    """

    DEFAULT_SYMBOL = "*"

    # Длина слова, которое может оказаться маской.
    MIN_LENGTH = 2
    MAX_LENGTH = 40

    async def extract(self, text: str, symbol: str = DEFAULT_SYMBOL) -> list[dict]:
        """
        Находит в тексте маски артикулов.

        Маской считается слово из латинских букв, цифр и символа подстановки,
        в котором есть хотя бы один символ подстановки и хотя бы один обычный
        символ. Порядок масок соответствует первому появлению в тексте,
        дубликаты убираются, регистр приводится к верхнему.

        Args:
            text: Текст документа.
            symbol: Символ подстановки, обозначающий один произвольный символ артикула.

        Returns:
            Список из одного элемента. Если масок нет, поле masks содержит пустой список.
        """
        if not isinstance(text, str):
            raise TypeError("text должен иметь тип str.")

        if len(symbol) != 1 or symbol.isalnum() or symbol.isspace():
            raise ValueError(
                "symbol должен быть одним символом: не буквой, не цифрой и не пробелом."
            )

        # Поиск выполняется в отдельном потоке: текст документа может быть большим.
        masks = await asyncio.to_thread(self._find_masks, text, symbol)

        return [
            {
                "manufacture": None,
                "group": None,
                "symbol": symbol,
                "masks": masks,
            }
        ]

    @classmethod
    def _find_masks(cls, text: str, symbol: str) -> list[str]:
        """Возвращает уникальные маски в порядке первого появления."""
        sym = re.escape(symbol)

        # Слово из букв, цифр и символа подстановки, не прилегающее к другим таким
        # символам. \w юникодный: слово с кириллицей внутри целиком отвергается,
        # а не разрезается на части.
        candidate = re.compile(
            rf"(?<![\w{sym}])[\w{sym}]{{{cls.MIN_LENGTH},{cls.MAX_LENGTH}}}(?![\w{sym}])"
        )
        valid = re.compile(rf"[A-Z0-9{sym}]+")

        masks: dict[str, None] = {}

        for match in candidate.finditer(text):
            token = match.group().upper()

            # Нужен хотя бы один символ подстановки и хотя бы один обычный символ.
            if symbol not in token or not token.replace(symbol, ""):
                continue

            if valid.fullmatch(token):
                masks.setdefault(token, None)

        return list(masks)