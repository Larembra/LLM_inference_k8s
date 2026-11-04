#!/usr/bin/env python3
"""Проверки безопасности и приватности после деплоя (нужен port-forward на :8000).

  1. без токена -> 401
  2. с токеном  -> 200
  3. в логах vLLM нет canary-промпта, начала ответа и самого токена; факт обращения есть
  4. токен не виден в argv процесса (/proc/1/cmdline)
"""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ask import DEFAULT_MODEL, NAMESPACE, get_token  # noqa: E402

BASE = os.getenv("LLM_BASE_URL", "http://localhost:8000")
OPENER = build_opener(ProxyHandler({}))
failures = 0


def report(ok: bool, name: str, detail: str = "") -> None:
    global failures
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def call(path: str, token: str | None = None, payload: dict | None = None) -> tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload else None
    request = Request(BASE + path, data=data, headers=headers, method="POST" if payload else "GET")
    try:
        with OPENER.open(request, timeout=900) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except (URLError, OSError) as error:
        return 0, str(error)


def main() -> int:
    token = get_token(interactive=False)
    if not token:
        print("Не удалось получить токен (LLM_API_TOKEN или доступ kubectl к Secret).")
        return 2

    status, _ = call("/v1/models")
    report(status == 401, "запрос без токена отклонён", f"HTTP {status}")

    canary = f"CANARY-{secrets.token_hex(8)}"
    status, body = call("/v1/chat/completions", token, {
        "model": DEFAULT_MODEL, "max_tokens": 32,
        "messages": [{"role": "user", "content": f"Say hello in a few words. Ref: {canary}"}],
    })
    report(status == 200, "запрос с токеном принят", f"HTTP {status}")
    answer = ""
    if status == 200:
        answer = json.loads(body)["choices"][0]["message"]["content"].strip()

    time.sleep(2)
    logs = subprocess.run(
        ["kubectl", "logs", "-n", NAMESPACE, "deployment/vllm", "-c", "vllm", "--tail=-1"],
        capture_output=True, text=True,
    ).stdout
    report(bool(logs), "логи получены")
    report(canary not in logs, "промпт (canary) отсутствует в логах")
    snippet = answer[:20]
    report(len(snippet) < 12 or snippet not in logs, "начало ответа модели отсутствует в логах")
    report(token not in logs, "API-токен отсутствует в логах")
    report("request-audit" in logs and "path=/v1/chat/completions" in logs,
           "факт обращения к модели залогирован")

    cmd = subprocess.run(
        ["kubectl", "exec", "-n", NAMESPACE, "deployment/vllm", "-c", "vllm", "--", "cat", "/proc/1/cmdline"],
        capture_output=True, text=True,
    )
    if cmd.returncode == 0:
        report(token not in cmd.stdout, "API-токен отсутствует в argv процесса")
    else:
        print("[SKIP] не удалось прочитать /proc/1/cmdline:", cmd.stderr.strip()[:120])

    print("\nИтого: " + ("всё OK" if failures == 0 else f"провалено проверок: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
