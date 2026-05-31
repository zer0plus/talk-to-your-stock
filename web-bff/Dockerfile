FROM python:3.12-slim

WORKDIR /workspace
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY shared ./shared
COPY web-bff ./web-bff
ENV PYTHONPATH=/workspace/web-bff:/workspace/shared

CMD ["uvicorn", "web_bff.main:app", "--host", "0.0.0.0", "--port", "8000"]
