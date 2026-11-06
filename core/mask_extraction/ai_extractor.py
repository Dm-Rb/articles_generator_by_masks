"""Модуль содержит функционал взаимодействия с ИИ-агентом"""

import json
import os
from pathlib import Path

from openai import AsyncOpenAI



class AIAgentError(Exception):
    """Ошибка при взаимодействии с ИИ-агентом."""


class DeepSeekAgent:
    """
    Класс для взаимодействия с DeepSeek: отправляет текст документа
    и возвращает ответ агента строкой.

    Агент возвращает JSON-объект следующей структуры:
    {
        "records": [
            {
                "manufacture": str | None,
                "group": str | None,
                "symbol": str,
                "masks": [str, ...]
            }
        ]
    }

    Метод ask возвращает ответ строкой как есть, метод extract разбирает его
    и проверяет структуру.
    """
    
    DEFAULT_MODEL = "deepseek-v4-flash"
    # Системный промпт хранится в отдельном файле .md
    DEFAULT_PROMPT_PATH = Path(__file__).with_name("mask_extraction_prompt.md")
    DEFAULT_TIMEOUT_SECONDS = 300

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        """
        Args:
            api_key: Ключ DeepSeek API. Если не указан, берётся из переменной
                окружения DEEPSEEK_API_KEY.
            model: Название модели.
            prompt_path: Путь к файлу с системным промптом (UTF-8).
            timeout: Таймаут запроса в секундах.
        """
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")

        if not api_key:
            raise AIAgentError(
                "Не указан ключ DeepSeek API: передайте api_key "
                "или задайте переменную окружения DEEPSEEK_API_KEY."
            )

        self._model = model
        self._system_prompt = self._load_prompt(Path(prompt_path))
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=timeout,
        )

    async def ask(self, text: str) -> str:
        """
        Отправляет текст документа агенту и возвращает его ответ.
        Args:
            text: Текст документа (результат DocumentReader).
        Returns:
            JSON-строка с ответом агента.
        Raises:
            AIAgentError: Запрос не удался, ответ пустой или обрезан.
        """
        if not isinstance(text, str):
            raise TypeError("text должен иметь тип str.")

        if not text.strip():
            raise AIAgentError("Нельзя отправить агенту пустой текст.")

        # Теги отделяют данные от инструкций:
        # системный промпт ссылается на них.
        contents = f"<document>\n{text}\n</document>"

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": self._system_prompt,
                    },
                    {
                        "role": "user",
                        "content": contents,
                    },
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )

        except Exception as exc:
            raise AIAgentError(
                f"Ошибка запроса к DeepSeek: {exc}"
            ) from exc

        if not response.choices:
            raise AIAgentError(
                "DeepSeek вернул ответ без вариантов генерации."
            )

        choice = response.choices[0]
        finish_reason = choice.finish_reason

        # Обрезанный JSON нельзя разобрать. Страховка
        if finish_reason == "length":
            raise AIAgentError(
                "Ответ DeepSeek обрезан из-за лимита длины. "
                "Документ слишком большой для одного запроса."
            )
        answer = choice.message.content
        if not answer or not answer.strip():
            raise AIAgentError(
                "DeepSeek вернул пустой ответ"
                + (
                    f" (причина завершения: {finish_reason})."
                    if finish_reason
                    else "."
                )
            )

        return answer

    async def extract(self, text: str) -> list[dict]:
        """
        Извлекает маски из текста документа и возвращает проверенный список.
        Returns:
            Список словарей с полями manufacture, group, symbol, masks.
            Пустой список, если масок в документе нет.
        Raises:
            AIAgentError: Запрос не удался или ответ не соответствует структуре.
        """
        answer = await self.ask(text)
        
        return self._parse_answer(answer)

    @classmethod
    def _parse_answer(cls, answer: str) -> list[dict]:
        """Разбирает JSON-ответ агента и проверяет его структуру."""
        try:
            data = json.loads(answer)
        except json.JSONDecodeError as exc:
            raise AIAgentError(
                f"Ответ DeepSeek не является корректным JSON: {exc}"
            ) from exc

        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise AIAgentError(
                'Ответ DeepSeek имеет неверную структуру: ожидался объект с полем "records".'
            )

        records = []

        for number, item in enumerate(data["records"], start=1):
            record = cls._parse_record(item, number)

            # Элемент без масок бесполезен для генерации.
            if record["masks"]:
                records.append(record)

        return records

    @staticmethod
    def _parse_record(item: object, number: int) -> dict:
        """Проверяет один элемент ответа и приводит его к стандартному виду."""
        if not isinstance(item, dict):
            raise AIAgentError(f"Элемент №{number} ответа DeepSeek не является объектом.")

        result = {}

        for field in ("manufacture", "group"):
            value = item.get(field)

            if value is not None and not isinstance(value, str):
                raise AIAgentError(
                    f"В элементе №{number} поле {field} должно быть строкой или null."
                )

            # Пустая строка равносильна отсутствию значения.
            result[field] = " ".join(value.split()) or None if value else None
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or len(symbol) != 1:
            raise AIAgentError(
                f"В элементе №{number} поле symbol должно быть строкой из одного символа."
            )
        result["symbol"] = symbol

        masks = item.get("masks")

        if not isinstance(masks, list) or not all(isinstance(m, str) for m in masks):
            raise AIAgentError(
                f"В элементе №{number} поле masks должно быть списком строк."
            )
        # Дубли убираются с сохранением порядка первого появления.
        result["masks"] = list(dict.fromkeys(m.strip() for m in masks if m.strip()))

        return result

    @staticmethod
    def _load_prompt(path: Path) -> str:
        """Читает системный промпт из файла."""
        if not path.is_file():
            raise AIAgentError(
                f"Файл системного промпта не найден: {path}"
            )
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise AIAgentError(
                f"Файл системного промпта пуст: {path}"
            )

        return prompt

    async def close(self) -> None:
        """Закрывает HTTP-клиент."""
        await self._client.close()
        