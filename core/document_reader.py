"""Модуль содержит функционал чтения файлов."""

import asyncio
from pathlib import Path
from io import BytesIO, StringIO
import csv


class DocumentReaderError(Exception):
    """Базовая ошибка при чтении документа."""


class UnsupportedFormatError(DocumentReaderError):
    """Формат файла не поддерживается."""


class EmptyDocumentError(DocumentReaderError):
    """Не удалось извлечь текст из документа."""


class ScannedPDFError(DocumentReaderError):
    """PDF не содержит текстового слоя."""


class DocumentReader:
    """
    Класс для извлечения текста из документов.
    """
    # Поддерживаемые форматы
    SUPPORTED_FORMATS = {
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".docx",
        ".xlsx",
        ".pdf",
    }

    async def read_from_bytes(self, data: bytes, filename: str) -> str:
        """
        Извлекает текст из документа, переданного в bytes.
        Args:
            data: Содержимое файла.
            filename: Имя файла с расширением.
        Returns:
            Извлечённый текст.
        """
        if not isinstance(data, bytes):
            raise TypeError("data должен иметь тип bytes.")

        if not filename:
            raise ValueError("Необходимо указать имя файла.")

        extension = Path(filename).suffix.lower()

        if extension not in self.SUPPORTED_FORMATS:
            raise UnsupportedFormatError(
                f"Формат {extension or 'без расширения'} не поддерживается."
            )

        # Извлечение текста выполняется в отдельном потоке потому, что библиотеки извлечения синхронные
        text = await asyncio.to_thread(
            self._extract_text,
            data,
            extension,
        )

        if not text.strip():
            raise EmptyDocumentError(
                "Документ прочитан, но текст в нём отсутствует."
            )

        return text

    async def read_from_file(self, file_path: str | Path) -> str:
        """
        Асинхронно извлекает текст из файла на диске.

        Args:
            file_path: Путь к файлу.

        Returns:
            Извлечённый текст.
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"Файл не найден: {path}")

        if not path.is_file():
            raise ValueError(
                f"Указанный путь не является файлом: {path}"
            )

        # Чтение файла и извлечение текста выполняются в отдельном потоке.
        data = await asyncio.to_thread(path.read_bytes)

        return await self.read_from_bytes(
            data=data,
            filename=path.name,
        )

    async def read(
        self,
        source: bytes | str | Path,
        filename: str | None = None,
    ) -> str:
        """
        Универсальный асинхронный метод чтения.

        Принимает:
        - bytes + filename;
        - str или Path (путь к файлу).
        """
        if isinstance(source, bytes):
            if filename is None:
                raise ValueError(
                    "При передаче bytes необходимо указать filename."
                )

            return await self.read_from_bytes(source, filename)

        if isinstance(source, (str, Path)):
            return await self.read_from_file(source)

        raise TypeError(
            "source должен иметь тип bytes, str или Path."
        )

    @staticmethod
    def _extract_text(data: bytes, extension: str) -> str:
        """Выбирает обработчик в зависимости от формата файла."""

        extractors = {
            ".txt": DocumentReader._extract_txt,
            ".md": DocumentReader._extract_txt,
            ".csv": lambda content: DocumentReader._extract_csv(content, ","), # Тут может быть косяк из-за разделителя
            ".tsv": lambda content: DocumentReader._extract_csv(content, "\t"),
            ".docx": DocumentReader._extract_docx,
            ".xlsx": DocumentReader._extract_xlsx,
            ".pdf": DocumentReader._extract_pdf,
        }

        return extractors[extension](data)

    @staticmethod
    def _decode_text(data: bytes) -> str:
        """Декодирует текст с поддержкой распространённых кодировок."""

        encodings = (
            "utf-8-sig",
            "utf-8",
            "cp1251",
            "utf-16",
        )

        for encoding in encodings:
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue

        raise DocumentReaderError(
            "Не удалось определить кодировку текстового файла."
        )

    @staticmethod
    def _extract_txt(data: bytes) -> str:
        return DocumentReader._decode_text(data)

    @staticmethod
    def _extract_csv(data: bytes, delimiter: str) -> str:
        """Преобразует CSV/TSV в текст."""

        content = DocumentReader._decode_text(data)

        reader = csv.reader(
            StringIO(content),
            delimiter=delimiter,
        )

        rows = [
            "\t".join(row)
            for row in reader
        ]

        return "\n".join(rows)

    @staticmethod
    def _extract_docx(data: bytes) -> str:
        """Извлекает абзацы и таблицы из DOCX."""

        try:
            from docx import Document
        except ImportError as exc:
            raise DocumentReaderError(
                "Установите библиотеку: pip install python-docx"
            ) from exc

        document = Document(BytesIO(data))
        parts = []

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()

            if text:
                parts.append(text)

        for table in document.tables:
            for row in table.rows:
                cells = [
                    cell.text.strip()
                    for cell in row.cells
                ]

                parts.append("\t".join(cells))

        return "\n".join(parts)

    @staticmethod
    def _extract_xlsx(data: bytes) -> str:
        """Извлекает значения ячеек со всех листов XLSX."""

        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise DocumentReaderError(
                "Установите библиотеку: pip install openpyxl"
            ) from exc

        workbook = load_workbook(
            BytesIO(data),
            read_only=True,
            data_only=True,
        )

        parts = []

        try:
            for worksheet in workbook.worksheets:
                parts.append(f"Лист: {worksheet.title}")

                for row in worksheet.iter_rows(values_only=True):
                    cells = [
                        "" if value is None else str(value)
                        for value in row
                    ]

                    if any(cell.strip() for cell in cells):
                        parts.append("\t".join(cells))

        finally:
            workbook.close()

        return "\n".join(parts)

    @staticmethod
    def _extract_pdf(data: bytes) -> str:
        """Извлекает текст из PDF с текстовым слоем."""

        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise DocumentReaderError(
                "Установите библиотеку: pip install pypdf"
            ) from exc

        try:
            reader = PdfReader(BytesIO(data))

            if reader.is_encrypted:
                raise DocumentReaderError(
                    "PDF защищён паролем."
                )

            pages = []

            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""

                if text.strip():
                    pages.append(
                        f"Страница {page_number}\n{text}"
                    )

            result = "\n\n".join(pages)

        except DocumentReaderError:
            raise

        except Exception as exc:
            raise DocumentReaderError(
                f"Не удалось прочитать PDF: {exc}"
            ) from exc

        if not result.strip():
            raise ScannedPDFError(
                "В PDF не найден текстовый слой. "
                "Вероятно, документ является сканом. "
                "Для его чтения потребуется OCR."
            )

        return result
