FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DOCKER_MODE=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY web_app/ ./web_app/
COPY settings.py .

RUN mkdir -p /app/tmp

EXPOSE 10111

CMD ["sh", "-c", "python -m uvicorn web_app.main:app --host \"$HOST\" --port \"$PORT\""]