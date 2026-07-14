FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY alembic.ini /app/alembic.ini
COPY shared /app/shared
COPY web-bff /app/web-bff
COPY agent-service /app/agent-service
COPY comps-service /app/comps-service

ENV PYTHONPATH=/app/shared:/app/web-bff:/app/agent-service:/app/comps-service
