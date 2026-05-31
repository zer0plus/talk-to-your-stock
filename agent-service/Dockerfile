FROM python:3.12-slim

WORKDIR /workspace
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY agent-service ./agent-service
ENV PYTHONPATH=/workspace/agent-service:/workspace/shared

CMD ["uvicorn", "agent_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
