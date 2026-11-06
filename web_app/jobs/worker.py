"""Точка входа дочернего процесса генерации.

Модуль намеренно лёгкий: при запуске через spawn дочерний процесс
импортирует его заново, поэтому веб-фреймворк здесь не подключается.
"""

from multiprocessing.connection import Connection


def run_job(records: list[dict], output_dir: str, conn: Connection) -> None:
    """Выполняет generate_archive_job и отправляет результат родителю."""
    result = {"status": "error", "message": "Процесс генерации не вернул результат."}

    try:
        from core.article_generator import generate_archive_job

        result = generate_archive_job(records, output_dir)

    except BaseException as error:  # noqa: BLE001
        result = {"status": "error", "message": str(error) or type(error).__name__}

    finally:
        try:
            conn.send(result)
        finally:
            conn.close()
