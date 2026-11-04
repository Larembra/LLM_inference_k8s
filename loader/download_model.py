#!/usr/bin/env python3
"""Загрузка весов в PVC.

Если в томе есть маркер .download-complete, совпадающий с конфигом, скрипт завершается
БЕЗ обращения к сети (huggingface_hub даже не импортируется). Иначе — скачивает
закреплённую ревизию и в конце пишет маркер (защита от недокачанных файлов).
"""
import json
import os
import sys
import time
from pathlib import Path

MARKER = ".download-complete"
DEFAULT_PATTERNS = ["*.json", "*.safetensors", "*.txt", "*.model", "tokenizer*"]


def log(message: str) -> None:
    print(f"[download-model] {message}", flush=True)


def main() -> int:
    model_id = os.environ["MODEL_ID"]
    requested_revision = os.environ.get("MODEL_REVISION") or "main"
    dest = Path(os.environ["MODEL_PATH"])
    patterns = [p.strip() for p in os.environ.get("MODEL_ALLOW_PATTERNS", "").split(",") if p.strip()] \
        or DEFAULT_PATTERNS
    marker = dest / MARKER

    if marker.is_file():
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
        if (meta.get("model_id") == model_id
                and meta.get("requested_revision") == requested_revision
                and (dest / "config.json").is_file()):
            log(f"model already present ({model_id}@{meta.get('resolved_sha')}); skipping, no network access")
            return 0
        log("marker does not match current config; re-downloading")
        marker.unlink()

    from huggingface_hub import HfApi, snapshot_download  # ленивый импорт: офлайн-путь выше его не требует

    token = os.environ.get("HF_TOKEN") or None
    sha = HfApi(token=token).model_info(model_id, revision=requested_revision).sha
    log(f"downloading {model_id}@{sha} (requested: {requested_revision}) to {dest}")
    if requested_revision == "main":
        log(f"TIP: pin it — set MODEL_REVISION={sha} in k8s/base/inference.env")

    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=model_id, revision=sha, local_dir=str(dest),
                      token=token, allow_patterns=patterns)

    if not (dest / "config.json").is_file() or not any(dest.glob("*.safetensors")):
        log("ERROR: config.json or *.safetensors missing after download; "
            "adjust MODEL_ALLOW_PATTERNS if the model uses another weight format")
        return 1

    marker.write_text(json.dumps({
        "model_id": model_id,
        "requested_revision": requested_revision,
        "resolved_sha": sha,
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }), encoding="utf-8")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
