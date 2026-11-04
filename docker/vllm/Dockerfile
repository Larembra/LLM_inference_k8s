# syntax=docker/dockerfile:1.7
FROM vllm/vllm-openai-cpu:v0.27.0@sha256:16981b40bb538e9127ad82e74a9f5386fddb4071c141725309d7ca4e2ea5872e

COPY --chmod=0444 requirements.txt lock.py /opt/lock/
RUN python /opt/lock/lock.py check /opt/lock/requirements.txt

COPY --chmod=0444 request_audit.py logging_config.json /opt/audit/
COPY --chmod=0444 start.sh /opt/audit/start.sh
RUN chmod 0755 /opt/audit && chmod 0444 /opt/audit/start.sh

ENV PYTHONPATH=/opt/audit \
    VLLM_LOGGING_CONFIG_PATH=/opt/audit/logging_config.json \
    VLLM_CONFIGURE_LOGGING=1 \
    VLLM_NO_USAGE_STATS=1 \
    DO_NOT_TRACK=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    VLLM_CACHE_ROOT=/tmp/vllm

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/bin/sh", "/opt/audit/start.sh"]