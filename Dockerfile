FROM vllm/vllm-openai-cpu:v0.27.0

USER 10001:0
ENV HOME=/tmp
EXPOSE 8000
ENTRYPOINT ["python", "-m", "vllm.entrypoints.openai.api_server"]
