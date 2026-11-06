"""Роутер страниц."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request):
    """Единственная страница SPA."""
    return templates.TemplateResponse(request, "index.html")
