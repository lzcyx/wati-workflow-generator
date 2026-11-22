FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN python -m pip install --no-cache-dir . \
    && mkdir logs \
    && chown 10001:10001 logs

USER 10001:10001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
