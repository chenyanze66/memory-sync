ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app
COPY requirements.txt .
# PIP_INDEX_URL / PIP_TRUSTED_HOST are overridable at build time
# (the ECS deployment uses the Aliyun mirror + a private ACR base image).
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} --trusted-host ${PIP_TRUSTED_HOST} -r requirements.txt
COPY app ./app

USER app
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "16", "--timeout-keep-alive", "10", "--no-access-log"]
