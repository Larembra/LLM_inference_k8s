import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_token() -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("LLM_API_TOKEN="):
            return line.partition("=")[2].strip().strip("\"").strip("'")
    return None


def save_token() -> int:
    token = input("API token: ").strip()
    if not token:
        print("Token cannot be empty.", file=sys.stderr)
        return 2
    ENV_FILE.write_text(f"LLM_API_TOKEN={token}\n", encoding="utf-8")
    print("Token saved to local .env file.")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "set-token":
        return save_token()

    parser = argparse.ArgumentParser(prog="ask", description="Отправить запрос локальной LLM")
    parser.add_argument("prompt", nargs="+", help="Текст запроса")
    parser.add_argument("--max-tokens", type=int, default=100)
    args = parser.parse_args()

    token = os.getenv("LLM_API_TOKEN") or load_token()
    if not token:
        print("Set the API token first with: python .\\ask.py set-token", file=sys.stderr)
        return 2

    endpoint = os.getenv("LLM_API_URL", "http://localhost:8000/v1/chat/completions")
    payload = {
        "model": "Qwen/Qwen2.5-1.5B-Instruct",
        "messages": [{"role": "user", "content": " ".join(args.prompt)}],
        "max_tokens": args.max_tokens,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=1800) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        print(f"API вернул HTTP {error.code}: {details}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, ConnectionError, OSError) as error:
        print(f"Не удалось обратиться к API: {error}", file=sys.stderr)
        return 1
    except (KeyError, IndexError, json.JSONDecodeError) as error:
        print(f"Не удалось разобрать ответ API: {error}", file=sys.stderr)
        return 1

    print(result["choices"][0]["message"]["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
