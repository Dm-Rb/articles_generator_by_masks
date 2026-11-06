"""Потоковая генерация артикулов по маскам"""
from __future__ import annotations

import csv
import hashlib
import itertools
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Literal, Sequence

DEFAULT_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
OutputFormat = Literal["txt", "csv"]

# Запрещённые символы Windows учитываются независимо от ОС, чтобы имена
# файлов оставались переносимыми между Windows, Linux и macOS.
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class GenerationError(ValueError):
    """Ошибка в данных, масках, алфавите или параметрах генерации."""


@dataclass(frozen=True)
class GenerationStats:
    """Статистика одной генерации."""

    masks_count: int
    articles_count: int
    alphabet_size: int


@dataclass(frozen=True)
class GeneratedFile:
    """Результат генерации для одной записи сертификата."""

    manufacture: str
    group: str
    path: Path
    stats: GenerationStats


def normalize_symbol(symbol: str) -> str:
    """Проверяет маркер подстановки: ровно один неалфавитно-цифровой символ."""
    if not isinstance(symbol, str) or len(symbol) != 1:
        raise GenerationError("symbol должен быть строкой ровно из одного символа.")
    if symbol.isalnum():
        raise GenerationError("symbol не может быть буквой или цифрой: он должен отличаться от алфавита артикулов.")
    if symbol.isspace() or ord(symbol) < 32:
        raise GenerationError("symbol не может быть пробелом или управляющим символом.")
    return symbol


def normalize_masks(masks: Iterable[str], symbol: str = "*") -> list[str]:
    """Проверяет маски и удаляет дубликаты, сохраняя исходный порядок."""
    marker = normalize_symbol(symbol)
    allowed_alphanumeric = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    result: list[str] = []
    seen: set[str] = set()

    for number, raw_mask in enumerate(masks, start=1):
        if not isinstance(raw_mask, str):
            raise GenerationError(f"Маска №{number} должна быть строкой.")

        mask = raw_mask.strip().upper()
        if not mask:
            raise GenerationError(f"Маска №{number} пустая.")
        if marker not in mask:
            raise GenerationError(
                f"Маска {raw_mask!r} не содержит маркер подстановки {marker!r}."
            )

        invalid = sorted(set(mask) - allowed_alphanumeric - {marker})
        if invalid:
            chars = " ".join(repr(char) for char in invalid)
            raise GenerationError(
                f"Маска {raw_mask!r} содержит неподдерживаемые символы: {chars}."
            )

        if mask not in seen:
            seen.add(mask)
            result.append(mask)

    if not result:
        raise GenerationError("Не передано ни одной маски для генерации.")
    return result


def normalize_alphabet(
    alphabet: str = DEFAULT_ALPHABET,
    exclude_chars: str = "",
) -> str:
    """Проверяет алфавит и исключает указанные символы."""
    if not isinstance(alphabet, str) or not alphabet:
        raise GenerationError("Алфавит должен быть непустой строкой.")
    if not isinstance(exclude_chars, str):
        raise GenerationError("exclude_chars должен быть строкой.")

    normalized = alphabet.upper()
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    invalid = sorted(set(normalized) - allowed)
    if invalid:
        raise GenerationError(
            "Алфавит содержит неподдерживаемые символы: "
            + " ".join(repr(char) for char in invalid) + "."
        )
    if len(set(normalized)) != len(normalized):
        raise GenerationError("Алфавит не должен содержать повторяющиеся символы.")

    excluded = exclude_chars.upper()
    invalid_excluded = sorted(set(excluded) - allowed)
    if invalid_excluded:
        raise GenerationError(
            "Список исключаемых символов содержит недопустимые символы: "
            + " ".join(repr(char) for char in invalid_excluded) + "."
        )

    result = "".join(char for char in normalized if char not in set(excluded))
    if not result:
        raise GenerationError("После исключения символов алфавит оказался пустым.")
    return result


def estimate_count(
    masks: Iterable[str],
    alphabet_size: int = 36,
    symbol: str = "*",
) -> int:
    """Возвращает точное количество результатов без их генерации."""
    if not isinstance(alphabet_size, int) or isinstance(alphabet_size, bool) or alphabet_size < 1:
        raise GenerationError("alphabet_size должен быть положительным целым числом.")
    normalized_masks = normalize_masks(masks, symbol)
    return sum(alphabet_size ** mask.count(symbol.upper()) for mask in normalized_masks)


def iter_articles(
    masks: Iterable[str],
    *,
    alphabet: str = DEFAULT_ALPHABET,
    exclude_chars: str = "",
    symbol: str = "*",
) -> Iterator[str]:
    """Лениво перебирает все артикулы по маскам."""
    marker = normalize_symbol(symbol)
    normalized_masks = normalize_masks(masks, marker)
    symbols = normalize_alphabet(alphabet, exclude_chars)

    for mask in normalized_masks:
        wildcard_count = mask.count(marker)
        for replacement in itertools.product(symbols, repeat=wildcard_count):
            replacement_iter = iter(replacement)
            yield "".join(next(replacement_iter) if char == marker else char for char in mask)


def sanitize_filename_part(value: str) -> str:
    """Заменяет недопустимые для переносимого имени файла символы на '_'."""
    if not isinstance(value, str):
        raise GenerationError("Значения manufacture и group должны быть строками.")

    cleaned = _INVALID_FILENAME_CHARS.sub("_", value)
    # Windows запрещает точки и пробелы в конце компонента имени.
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        cleaned = "_"
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def build_output_filename(manufacture: str, group: str) -> str:
    """Формирует безопасное имя CSV-файла из производителя и группы."""
    safe_manufacture = sanitize_filename_part(manufacture)
    safe_group = sanitize_filename_part(group)
    return f"{safe_manufacture}__{safe_group}.csv"


def write_articles(
    masks: Iterable[str],
    output_path: str | Path,
    *,
    output_format: OutputFormat = "txt",
    alphabet: str = DEFAULT_ALPHABET,
    exclude_chars: str = "",
    csv_header: bool = True,
    symbol: str = "*",
) -> GenerationStats:
    """Потоково записывает артикулы в TXT или одноколоночный CSV."""
    if output_format not in ("txt", "csv"):
        raise GenerationError("output_format должен быть 'txt' или 'csv'.")

    marker = normalize_symbol(symbol)
    normalized_masks = normalize_masks(masks, marker)
    symbols = normalize_alphabet(alphabet, exclude_chars)
    count = 0
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="" if output_format == "csv" else "\n") as file:
        if output_format == "csv":
            writer = csv.writer(file)
            if csv_header:
                writer.writerow(["article"])
            for article in iter_articles(normalized_masks, alphabet=symbols, symbol=marker):
                writer.writerow([article])
                count += 1
        else:
            for article in iter_articles(normalized_masks, alphabet=symbols, symbol=marker):
                file.write(article)
                file.write("\n")
                count += 1

    return GenerationStats(len(normalized_masks), count, len(symbols))


class ArticleGenerator:
    """Генерирует отдельный CSV для каждой записи результата извлечения."""

    def __init__(
        self,
        *,
        alphabet: str = DEFAULT_ALPHABET,
        exclude_chars: str = "",
    ) -> None:
        self.alphabet = normalize_alphabet(alphabet, exclude_chars)

    def iter_articles(self, masks: Iterable[str], symbol: str = "*") -> Iterator[str]:
        return iter_articles(masks, alphabet=self.alphabet, symbol=symbol)

    def estimate_count(self, masks: Iterable[str], symbol: str = "*") -> int:
        return estimate_count(masks, alphabet_size=len(self.alphabet), symbol=symbol)

    def generate(
        self,
        records: Sequence[dict[str, object]],
        output_dir: str | Path = "tmp",
    ) -> list[GeneratedFile]:
        """Создаёт по CSV-файлу на каждый словарь входного списка.

        Если разные записи после очистки manufacture/group дают одинаковое
        имя файла, к последующим именам добавляется суффикс __2, __3 и т.д.,
        чтобы не перезаписать ранее созданный результат.
        """
        if not isinstance(records, (list, tuple)):
            raise GenerationError("records должен быть списком словарей.")
        if not records:
            raise GenerationError("Список записей для генерации пуст.")

        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        used_names: set[str] = set()
        results: list[GeneratedFile] = []

        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise GenerationError(f"Элемент №{index} должен быть словарём.")

            manufacture = record.get("manufacture")
            group = record.get("group")
            symbol = record.get("symbol")
            masks = record.get("masks")

            if not isinstance(manufacture, str) or not manufacture.strip():
                raise GenerationError(f"В элементе №{index} отсутствует корректный manufacture.")
            if not isinstance(group, str) or not group.strip():
                raise GenerationError(f"В элементе №{index} отсутствует корректный group.")
            if not isinstance(masks, list):
                raise GenerationError(f"В элементе №{index} поле masks должно быть списком.")
            marker = normalize_symbol(symbol)  # type: ignore[arg-type]
            normalized_masks = normalize_masks(masks, marker)

            filename = build_output_filename(manufacture, group)
            stem = Path(filename).stem
            candidate = filename
            suffix = 2
            while candidate.casefold() in used_names:
                candidate = f"{stem}__{suffix}.csv"
                suffix += 1
            used_names.add(candidate.casefold())

            output_path = directory / candidate
            stats = write_articles(
                normalized_masks,
                output_path,
                output_format="csv",
                alphabet=self.alphabet,
                symbol=marker,
            )
            results.append(
                GeneratedFile(
                    manufacture=manufacture,
                    group=group,
                    path=output_path,
                    stats=stats,
                )
            )

        return results

    def archive_files(
        self,
        files: Sequence[GeneratedFile],
        archive_dir: str | Path | None = None,
    ) -> Path:
        """Упаковывает сгенерированные файлы в ZIP и удаляет их после успеха.
        Имя архива — 32 строчные латинские буквы, полученные из MD5-хеша
        текущего времени в наносекундах от начала эпохи Unix.
        Исходные файлы удаляются только после успешного закрытия ZIP-архива.
        """
        if not isinstance(files, (list, tuple)) or not files:
            raise GenerationError("files должен быть непустым списком GeneratedFile.")

        paths: list[Path] = []
        for index, generated_file in enumerate(files, start=1):
            if not isinstance(generated_file, GeneratedFile):
                raise GenerationError(
                    f"Элемент files №{index} должен быть объектом GeneratedFile."
                )
            path = Path(generated_file.path)
            if not path.is_file():
                raise GenerationError(f"Файл для архивации не найден: {path}")
            paths.append(path)

        destination_dir = Path(archive_dir) if archive_dir is not None else paths[0].parent
        destination_dir.mkdir(parents=True, exist_ok=True)

        # Переводим шестнадцатеричные символы MD5 (0-f) в буквы (a-p),
        # чтобы имя состояло ровно из 32 буквенных символов.
        timestamp = str(time.time_ns()).encode("ascii")
        digest = hashlib.md5(timestamp).hexdigest()
        letters = digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
        archive_path = destination_dir / f"{letters}.zip"

        try:
            with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in paths:
                    archive.write(path, arcname=path.name)
        except Exception:
            # Не оставляем частично созданный архив при ошибке упаковки.
            archive_path.unlink(missing_ok=True)
            raise

        # Удаление выполняется только после успешной записи и закрытия архива.
        for path in paths:
            path.unlink()

        return archive_path

    def write(
        self,
        masks: Iterable[str],
        output_path: str | Path,
        *,
        output_format: OutputFormat = "txt",
        csv_header: bool = True,
        symbol: str = "*",
    ) -> GenerationStats:
        """Низкоуровневый метод записи для одного списка масок."""
        return write_articles(
            masks,
            output_path,
            output_format=output_format,
            alphabet=self.alphabet,
            csv_header=csv_header,
            symbol=symbol,
        )

def generate_archive_job(records: list[dict], output_dir: str) -> dict:
    """
    Функция-обёртка. Запускается в отдельном процессе что бы не уронить весь поток выполнения 
    приложения на время вычислений.
    """
    try:
        generator = ArticleGenerator()

        results = generator.generate(
            records=records,
            output_dir=output_dir,
        )

        archive_path = generator.archive_files(results)

        return {
            "status": "ok",
            "message": str(Path(archive_path).resolve()),
        }

    except Exception as error:
        return {
            "status": "error",
            "message": str(error),
        }