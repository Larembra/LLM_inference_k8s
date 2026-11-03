FROM vllm/vllm-openai-cpu:v0.27.0@sha256:16981b40bb538e9127ad82e74a9f5386fddb4071c141725309d7ca4e2ea5872e

USER 10001:0
ENV HOME=/tmp
EXPOSE 8000
ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]
