"""Чтение переменных из .env и окружения"""
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parent / ".env"
DOCKER_MODE = os.getenv("DOCKER_MODE") == "1"

class SettingsError(Exception):
    """Некорректное содержимое .env или значения настроек."""


@dataclass(frozen=True)
class Settings:
    """
    Настройки приложения
    """

    # repr=False: ключ не должен попадать в логи и сообщения об ошибках.
    deepseek_api_key: str | None = field(repr=False)
    host: str
    port: int


def _parse_env_file(path: Path) -> dict[str, str]:
    """Читает файл формата KEY=VALUE. Отсутствующий файл считается пустым"""
    values: dict[str, str] = {}

    if not path.is_file():
        return values

    for number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            raise SettingsError(f"{path.name}, строка {number}: ожидается формат KEY=VALUE.")

        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # Комментарий в конце строки отделяется пробелом перед «#».
            value = value.split(" #", 1)[0].rstrip()

        values[key] = value

    return values


def load_settings(env_path: str | Path = ENV_PATH) -> Settings:
    """
    Загружает настройки.
    Переменные окружения имеют приоритет над значениями из .env.
    В Docker-режиме .env не используется.
    """
    file_values = {} if DOCKER_MODE else _parse_env_file(Path(env_path))

    def get(name: str) -> str | None:
        value = os.environ.get(name) or file_values.get(name)
        return value.strip() or None if value else None

    deepseek_api_key = get("DEEPSEEK_API_KEY")
    host = get("HOST")
    raw_port = get("PORT")

    if DOCKER_MODE:
        missing = [
            name
            for name, value in (
                ("DEEPSEEK_API_KEY", deepseek_api_key),
                ("HOST", host),
                ("PORT", raw_port),
            )
            if value is None
        ]
        if missing:
            raise SettingsError(
                f"В Docker-режиме необходимо задать переменные: {', '.join(missing)}."
            )

    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise SettingsError(
            f"PORT должен быть целым числом, получено: {raw_port!r}."
        ) from None

    if not 1 <= port <= 65535:
        raise SettingsError(
            f"PORT должен быть в диапазоне 1-65535, получено: {port}."
        )

    return Settings(
        deepseek_api_key=deepseek_api_key,
        host=host,
        port=port,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает настройки, загруженные один раз за время работы процесса."""
    return load_settings()
