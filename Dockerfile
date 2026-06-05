FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENPINE_ALLOW_PICKLE_STATE=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY accounts ./accounts
COPY adapters ./adapters
COPY artifacts ./artifacts
COPY batch ./batch
COPY cli ./cli
COPY compile ./compile
COPY config ./config
COPY contracts ./contracts
COPY daemon ./daemon
COPY data ./data
COPY domain ./domain
COPY events ./events
COPY execution ./execution
COPY export ./export
COPY gateway ./gateway
COPY jobs ./jobs
COPY notifications ./notifications
COPY optimizer ./optimizer
COPY orders ./orders
COPY pine ./pine
COPY recovery ./recovery
COPY registry ./registry
COPY risk ./risk
COPY runtime ./runtime
COPY state ./state
COPY storage ./storage
COPY streams ./streams
COPY workers ./workers
COPY __init__.py integrations.py exchange_metadata.py ./

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e .

EXPOSE 8080

CMD ["python", "-c", "from openpine.gateway.server import create_app; import uvicorn; uvicorn.run(create_app(), host='0.0.0.0', port=8080)"]
