# Tools server only. The loader runs on the host, against the raw export.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml ./
RUN pip install --no-cache-dir "duckdb>=1.0" "fastapi>=0.110" "uvicorn[standard]>=0.27" "pydantic>=2.6"

COPY db.py ./
COPY loader ./loader
COPY tools ./tools
COPY web ./web

ENV WEARABLE_DB=/data/wearable.duckdb TOOLS_HOST=0.0.0.0 TOOLS_PORT=8000
EXPOSE 8000
CMD ["uvicorn", "tools.server:app", "--host", "0.0.0.0", "--port", "8000"]
