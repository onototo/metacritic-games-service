FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app TZ=Europe/Minsk
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
