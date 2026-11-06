"""Роутер извлечения масок из загруженного документа"""
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from core.document_reader import DocumentReaderError, UnsupportedFormatError
from core.mask_extraction.ai_extractor import AIAgentError, DeepSeekAgent
from settings import get_settings

router = APIRouter(prefix="/api")

MODES = ("regex", "ai")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 МБ


@router.post("/extract")
async def extract(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form(...),
):
    """Читает документ и возвращает список записей {manufacture, group, symbol, masks}"""
    if mode not in MODES:
        raise HTTPException(422, "Неизвестный режим извлечения масок.")

    # Читаем на один байт больше лимита, чтобы отличить "ровно лимит" от "больше".
    data = await file.read(MAX_FILE_SIZE + 1)

    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            413, f"Файл слишком большой: максимум {MAX_FILE_SIZE // (1024 * 1024)} МБ."
        )

    text = await _read_text(request, data, file.filename or "")

    if mode == "ai":
        agent = await _get_ai_agent(request)

        try:
            records = await agent.extract(text)
        except AIAgentError as error:
            raise HTTPException(502, str(error)) from error

    else:
        records = await request.app.state.regex_extractor.extract(text)
        # Запись без масок для пользователя бесполезна
        records = [record for record in records if record["masks"]]

    return {"records": records}


async def _read_text(request: Request, data: bytes, filename: str) -> str:
    """Извлекает текст документа, переводя ошибки чтения в HTTP-ответы."""
    try:
        return await request.app.state.document_reader.read_from_bytes(data, filename)

    except UnsupportedFormatError as error:
        raise HTTPException(415, str(error)) from error

    except (DocumentReaderError, ValueError) as error:
        raise HTTPException(422, str(error)) from error

    except Exception as error:  # повреждённый docx/xlsx и т.п.
        raise HTTPException(422, f"Не удалось прочитать файл: {error}") from error


async def _get_ai_agent(request: Request) -> DeepSeekAgent:
    """Возвращает общий ИИ-агент, создавая его при первом обращении."""
    state = request.app.state

    async with state.ai_agent_lock:
        if state.ai_agent is None:
            try:
                token = get_settings().deepseek_api_key
                state.ai_agent = DeepSeekAgent(api_key=token)
            except AIAgentError as error:
                raise HTTPException(503, str(error)) from error

    return state.ai_agent
