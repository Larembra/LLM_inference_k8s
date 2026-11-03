FROM python:3.12.10-slim-bookworm
RUN pip install --no-cache-dir huggingface_hub==0.30.2
USER 10001:0
ENV HOME=/tmp HF_HOME=/models/.cache
ENTRYPOINT ["python", "/app/download_model.py"]
COPY download_model.py /app/download_model.py
