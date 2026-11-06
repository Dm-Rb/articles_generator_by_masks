import uvicorn
from settings import get_settings


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("web_app.main:app", host=settings.host, port=settings.port)
