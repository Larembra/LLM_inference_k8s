FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
USER 10001:0
ENV HOME=/tmp HF_HOME=/models/.cache
ENTRYPOINT ["python", "/app/download_model.py"]
COPY download_model.py /app/download_model.py
