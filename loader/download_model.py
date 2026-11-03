import os
from huggingface_hub import snapshot_download

model = os.environ.get("MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct")
destination = os.environ.get("MODEL_PATH", "/models/qwen")
token = os.environ.get("HF_TOKEN") or None

snapshot_download(
    repo_id=model,
    local_dir=destination,
    token=token,
    local_dir_use_symlinks=False,
)
