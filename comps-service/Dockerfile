FROM python:3.12-slim

WORKDIR /workspace
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY comps-service ./comps-service
ENV PYTHONPATH=/workspace/comps-service:/workspace/shared

CMD ["uvicorn", "comps_service.main:app", "--host", "0.0.0.0", "--port", "8002"]
