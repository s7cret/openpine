FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY openpine ./openpine

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e .

EXPOSE 8080

CMD ["python", "-c", "from openpine.gateway.server import create_app; import uvicorn; uvicorn.run(create_app(), host='0.0.0.0', port=8080)"]
