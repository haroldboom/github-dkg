FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml LICENSE README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

COPY entrypoint.py ./

ENTRYPOINT ["python", "/app/entrypoint.py"]
