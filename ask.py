#!/usr/bin/env python3
"""CLI-клиент к vLLM. Только стандартная библиотека, Python 3.10+.

Токен ищется по порядку:
  1. переменная окружения LLM_API_TOKEN;
  2. k8s Secret в кластере (через kubectl) — файлов на диске нет;
  3. скрытый интерактивный ввод (getpass), без сохранения.
"""
import argparse
import base64
import getpass
import json
import os
import shutil
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

NAMESPACE = os.getenv("LLM_K8S_NAMESPACE", "llm-inference")
SECRET_NAME = os.getenv("LLM_K8S_SECRET", "inference-secrets")
SECRET_KEY = "api-token"
DEFAULT_URL = "http://localhost:8000/v1/chat/completions"
DEFAULT_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")


def token_from_cluster() -> str | None:
    if not shutil.which("kubectl"):
        return None
    try:
        result = subprocess.run(
            ["kubectl", "get", "secret", SECRET_NAME, "-n", NAMESPACE,
             "-o", f"jsonpath={{.data.{SECRET_KEY}}}"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return base64.b64decode(result.stdout.strip()).decode("utf-8").strip()
    except ValueError:
        return None


def get_token(interactive: bool = True) -> str | None:
    token = os.getenv("LLM_API_TOKEN") or token_from_cluster()
    if token:
        return token
    if interactive and sys.stdin.isatty():
        return getpass.getpass("API token (не сохраняется): ").strip() or None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(prog="ask", description="Отправить запрос к LLM в k8s")
    parser.add_argument("prompt", nargs="+", help="Текст запроса")
    parser.add_argument("--max-tokens", type=int, default=100)
    parser.add_argument("--system", default=None, help="Системный промпт")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--url", default=os.getenv("LLM_API_URL", DEFAULT_URL))
    args = parser.parse_args()

    token = get_token()
    if not token:
        print("Токен не найден: задайте LLM_API_TOKEN или убедитесь, что kubectl "
              "видит Secret (см. scripts/create_secret.py).", file=sys.stderr)
        return 2

    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": " ".join(args.prompt)})
    payload = {"model": args.model, "messages": messages, "max_tokens": args.max_tokens}

    request = Request(
        args.url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=1800) as response:
            result = json.loads(response.read().decode("utf-8"))
        print(result["choices"][0]["message"]["content"])
        return 0
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        print(f"API вернул HTTP {error.code}: {details}", file=sys.stderr)
    except (URLError, TimeoutError, ConnectionError, OSError) as error:
        print(f"Не удалось обратиться к API: {error}", file=sys.stderr)
    except (KeyError, IndexError, json.JSONDecodeError) as error:
        print(f"Не удалось разобрать ответ API: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
