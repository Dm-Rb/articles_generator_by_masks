"""Менеджер задач генерации: один активный процесс, статусы, отмена."""

import asyncio
import multiprocessing
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from web_app.jobs.worker import run_job

# Сколько ждать штатной остановки процесса, прежде чем убить его принудительно.
TERMINATE_TIMEOUT_SECONDS = 10


class JobBusyError(Exception):
    """Генерация уже выполняется: одновременно допускается только одна."""


@dataclass
class Job:
    """Состояние одной задачи генерации."""

    id: str
    directory: Path
    status: str = "running"  # running | ok | error
    message: str = ""
    archive_path: Path | None = None
    cancelled: bool = False
    process: multiprocessing.process.BaseProcess | None = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)


class JobManager:
    """
    Запускает генерацию в отдельном процессе (spawn) и хранит статусы.

    Одновременно выполняется не более одной задачи. Статусы лежат в памяти
    процесса приложения, поэтому приложение нужно запускать одним worker.
    """

    def __init__(self, base_dir: str | Path = "tmp") -> None:
        self._base_dir = Path(base_dir)
        self._context = multiprocessing.get_context("spawn")
        self._jobs: dict[str, Job] = {}
        self._active_id: str | None = None

    @property
    def is_busy(self) -> bool:
        return self._active_id is not None

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def get_archive(self, job_id: str) -> Path | None:
        """Путь к готовому архиву или None, если его нет."""
        job = self._jobs.get(job_id)

        if job is None or job.status != "ok" or job.archive_path is None:
            return None

        return job.archive_path if job.archive_path.is_file() else None

    async def start(self, records: list[dict]) -> Job:
        """
        Запускает генерацию.

        Raises:
            JobBusyError: Уже выполняется другая генерация.
        """
        if self._active_id is not None:
            raise JobBusyError("Уже выполняется другая генерация.")

        job_id = uuid.uuid4().hex
        job = Job(id=job_id, directory=self._base_dir / job_id)

        # Слот занимается до первого await, чтобы два запроса не прошли одновременно.
        self._active_id = job.id
        self._jobs[job.id] = job

        try:
            parent_conn, child_conn = self._context.Pipe(duplex=False)
            job.process = self._context.Process(
                target=run_job,
                args=(records, str(job.directory), child_conn),
                daemon=True,
            )
            await asyncio.to_thread(job.process.start)
            # Копия конца канала в родителе закрывается, иначе при падении
            # дочернего процесса recv() не получит EOF.
            child_conn.close()

        except BaseException:
            self._release(job)
            self._jobs.pop(job.id, None)
            shutil.rmtree(job.directory, ignore_errors=True)
            raise

        job.task = asyncio.create_task(self._watch(job, parent_conn))
        return job

    async def cancel(self, job_id: str) -> bool:
        """Останавливает процесс (если идёт) и удаляет файлы задачи."""
        job = self._jobs.pop(job_id, None)

        if job is None:
            return False

        job.cancelled = True

        if job.task is not None and not job.task.done():
            job.process.terminate()

            try:
                await asyncio.wait_for(asyncio.shield(job.task), TERMINATE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                job.process.kill()
                await job.task

        # Файлы удаляются только после завершения процесса.
        await asyncio.to_thread(shutil.rmtree, job.directory, True)
        return True

    async def shutdown(self) -> None:
        """Останавливает активную задачу при выключении приложения."""
        if self._active_id is not None:
            await self.cancel(self._active_id)

    # ------------------------------------------------------------------

    async def _watch(self, job: Job, conn) -> None:
        """Ждёт результат дочернего процесса и обновляет статус задачи."""
        try:
            result, exit_code = await asyncio.to_thread(self._wait, job, conn)

            if job.cancelled:
                return

            if result is None:
                job.status = "error"
                job.message = (
                    "Процесс генерации завершился аварийно "
                    f"(код {exit_code}). Возможно, не хватило памяти или места на диске."
                )
            elif result.get("status") == "ok":
                job.status = "ok"
                job.archive_path = Path(result["message"])
            else:
                job.status = "error"
                job.message = str(result.get("message") or "Неизвестная ошибка генерации.")

        except Exception as error:  # noqa: BLE001
            job.status = "error"
            job.message = f"Ошибка контроля процесса генерации: {error}"

        finally:
            self._release(job)

    @staticmethod
    def _wait(job: Job, conn) -> tuple[dict | None, int | None]:
        """Блокирующее ожидание результата (выполняется в потоке)."""
        try:
            result = conn.recv()
        except (EOFError, OSError):
            result = None
        finally:
            conn.close()

        job.process.join()
        return result, job.process.exitcode

    def _release(self, job: Job) -> None:
        if self._active_id == job.id:
            self._active_id = None
