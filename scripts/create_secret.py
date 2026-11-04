#!/usr/bin/env python3
"""Создаёт/обновляет Secret inference-secrets.

Токены не попадают ни в argv, ни в историю shell, ни в файлы: манифест уходит
в `kubectl apply -f -` через stdin. API-токен генерируется случайно.

  python scripts/create_secret.py               # создать (если нет)
  python scripts/create_secret.py --hf-token    # + спросить HF-токен (скрытый ввод)
  python scripts/create_secret.py --rotate      # выпустить новый API-токен
"""
import argparse
import base64
import getpass
import json
import secrets
import subprocess
import sys
from pathlib import Path

NAMESPACE_MANIFEST = Path(__file__).resolve().parent.parent / "k8s" / "base" / "namespace.yaml"


def kubectl(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True)


def b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="llm-inference")
    parser.add_argument("--name", default="inference-secrets")
    parser.add_argument("--hf-token", action="store_true", help="запросить HF-токен (gated-модели)")
    parser.add_argument("--rotate", action="store_true", help="заменить API-токен новым")
    args = parser.parse_args()

    result = kubectl("apply", "-f", str(NAMESPACE_MANIFEST))
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1

    data: dict[str, str] = {}
    existing = kubectl("get", "secret", args.name, "-n", args.namespace, "-o", "json")
    if existing.returncode == 0:
        data = json.loads(existing.stdout).get("data") or {}

    changed = False
    if "api-token" not in data or args.rotate:
        data["api-token"] = b64(secrets.token_urlsafe(32))
        changed = True
    if args.hf_token:
        value = getpass.getpass("Hugging Face token (ввод скрыт): ").strip()
        if value:
            data["hf-token"] = b64(value)
            changed = True

    if not changed:
        print(f"Secret {args.name} уже существует; ничего не изменено (--rotate / --hf-token для изменения).")
        return 0

    manifest = {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": args.name, "namespace": args.namespace},
        "data": data,
    }
    result = kubectl("apply", "-f", "-", stdin=json.dumps(manifest))
    print(result.stdout.strip() or result.stderr.strip())
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
