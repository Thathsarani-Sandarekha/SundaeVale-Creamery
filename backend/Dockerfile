# Build context: repository root. The API and worker share this image.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements*.txt /app/backend/
RUN pip install -r backend/requirements-ocr.txt -r backend/requirements-aws.txt rapidocr onnxruntime pymupdf
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/.invoice_data && chown -R appuser:appuser /app
COPY --chown=appuser:appuser backend /app/backend
COPY --chown=appuser:appuser deploy/certs /app/certs
USER appuser
EXPOSE 8001
CMD ["uvicorn", "backend.invoice_processing.server:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
