FROM python:alpine

WORKDIR /app

RUN pip install pdm

COPY pyproject.toml pdm.lock /app/

RUN pdm install

COPY lento /app/

ENTRYPOINT [ ".venv/bin/uvicorn", "server:app", "--host", "0.0.0.0"]
