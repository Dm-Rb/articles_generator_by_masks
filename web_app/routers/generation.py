"""Роутер генерации артикулов: запуск задачи, статус, отмена, скачивание."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints

from web_app.jobs.manager import JobBusyError

router = APIRouter(prefix="/api")

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RecordIn(BaseModel):
    manufacture: NonEmptyStr
    group: NonEmptyStr
    symbol: str = Field(min_length=1, max_length=1)
    masks: list[NonEmptyStr] = Field(min_length=1)


class GenerateRequest(BaseModel):
    records: list[RecordIn] = Field(min_length=1)


@router.post("/generate")
async def generate(request: Request, body: GenerateRequest):
    """Запускает генерацию в отдельном процессе и сразу возвращает job_id.
        Отдельный процесс необходим что бы не уронить приложение
        """
    
    manager = request.app.state.job_manager

    try:
        job = await manager.start([record.model_dump() for record in body.records])
    except JobBusyError as error:
        raise HTTPException(409, "Уже выполняется другая генерация. Повторите позже.") from error

    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
async def job_status(request: Request, job_id: str):
    """Статус задачи: running, ok или error с текстом ошибки"""
    job = request.app.state.job_manager.get(job_id)

    if job is None:
        raise HTTPException(404, "Задача не найдена.")

    return {"status": job.status, "message": job.message}


@router.delete("/jobs/{job_id}", status_code=204)
async def cancel_job(request: Request, job_id: str):
    """Останавливает задачу если идёт и удаляет её файлы"""
    await request.app.state.job_manager.cancel(job_id)
    return Response(status_code=204)


@router.get("/download/{job_id}")
async def download(request: Request, job_id: str):
    """Отдаёт готовый ZIP-архив"""
    archive = request.app.state.job_manager.get_archive(job_id)

    if archive is None:
        raise HTTPException(404, "Архив не найден или ещё не готов.")

    return FileResponse(archive, media_type="application/zip", filename=archive.name)
