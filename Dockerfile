FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml LICENSE README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

COPY entrypoint.py ./

RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

ENTRYPOINT ["python", "/app/entrypoint.py"]
