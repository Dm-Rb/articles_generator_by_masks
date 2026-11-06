import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from core.document_reader import DocumentReader
from core.mask_extraction.re_extractor import MaskExtractor
from web_app.jobs.manager import JobManager
from web_app.routers import extraction, generation, pages

BASE_DIR = Path(__file__).resolve().parent
# Каталог задач лежит в корне проекта (рядом с core и web_app).
TMP_DIR = BASE_DIR.parent / "tmp"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Создаёт общие объекты приложения и освобождает ресурсы при остановке"""
    app.state.job_manager = JobManager(TMP_DIR)
    app.state.document_reader = DocumentReader()
    app.state.regex_extractor = MaskExtractor()
    # ИИ-агент создаётся лениво при первом запросе в режиме AI.
    app.state.ai_agent = None
    app.state.ai_agent_lock = asyncio.Lock()

    try:
        yield
    finally:
        await app.state.job_manager.shutdown()

        if app.state.ai_agent is not None:
            await app.state.ai_agent.close()


app = FastAPI(title="Генератор артикулов по маскам", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(pages.router)
app.include_router(extraction.router)
app.include_router(generation.router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Приводит ошибки валидации к виду {"detail": "текст"} для интерфейса"""
    first = exc.errors()[0]
    place = ".".join(str(part) for part in first["loc"] if part != "body")

    return JSONResponse(
        status_code=422,
        content={"detail": f"Некорректные данные запроса ({place}): {first['msg']}"},
    )
