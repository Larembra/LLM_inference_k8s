# syntax=docker/dockerfile:1.7
FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-deps -r requirements.txt && pip check

COPY --chmod=0444 download_model.py .

ENV HOME=/tmp HF_HOME=/tmp/hf
USER 10001:10001
ENTRYPOINT ["python", "/app/download_model.py"]