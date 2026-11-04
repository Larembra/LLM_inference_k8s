#!/usr/bin/env python3
"""Нагрузочный тест OpenAI-совместимого vLLM: TTFT, Latency, Throughput. Только stdlib.

Closed-loop: на каждом уровне параллелизма C одновременно «в полёте» ровно C запросов.
Каждый промпт уникален (случайный префикс), чтобы prefix cache не искажал результат.
Длина вывода фиксируется (ignore_eos), чтобы прогоны были сопоставимы.

Пример:
  kubectl port-forward -n llm-inference service/vllm 8000:8000
  python loadtest/loadtest.py --concurrency 1,2,4 --requests 8
"""
import argparse
import http.client
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ask import DEFAULT_MODEL, get_token  # noqa: E402

FILLER = "Kubernetes schedules containers onto nodes, restarts failed pods and exposes services. "  # ~17 токенов


@dataclass
class Result:
    ok: bool = False
    error: str = ""
    ttft: float = math.nan
    latency: float = math.nan
    prompt_tokens: int = 0
    completion_tokens: int = 0


def build_prompt(target_tokens: int) -> str:
    nonce = f"[{random.getrandbits(64):016x}] "
    if target_tokens <= 32:
        return nonce + "Explain what a PersistentVolumeClaim is."
    return nonce + FILLER * max(1, target_tokens // 17) + "\n\nSummarize the text above in one sentence."


def one_request(url: str, headers: dict, model: str, prompt: str, max_tokens: int,
                ignore_eos: bool, timeout: float) -> Result:
    parsed = urlparse(url)
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = cls(parsed.hostname, parsed.port, timeout=timeout)
    body = json.dumps({
        "model": model, "stream": True, "stream_options": {"include_usage": True},
        "max_tokens": max_tokens, "temperature": 0.0, "ignore_eos": ignore_eos,
        "messages": [{"role": "user", "content": prompt}],
    })
    result, chunks = Result(), 0
    started = time.perf_counter()
    try:
        conn.request("POST", parsed.path or "/", body, headers)
        response = conn.getresponse()
        if response.status != 200:
            result.error = f"HTTP {response.status}"
            return result
        while True:
            line = response.readline()
            if not line:
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                break
            event = json.loads(payload)
            if event.get("usage"):
                result.prompt_tokens = event["usage"].get("prompt_tokens", 0)
                result.completion_tokens = event["usage"].get("completion_tokens", 0)
            choices = event.get("choices") or []
            if choices and (choices[0].get("delta") or {}).get("content"):
                if math.isnan(result.ttft):
                    result.ttft = time.perf_counter() - started
                chunks += 1
        result.latency = time.perf_counter() - started
        result.completion_tokens = result.completion_tokens or chunks
        result.ok = not math.isnan(result.ttft)
        if not result.ok:
            result.error = "no tokens received"
    except (OSError, ValueError, http.client.HTTPException) as error:
        result.error = f"{type(error).__name__}: {error}"
    finally:
        conn.close()
    return result


def pct(values: list[float], p: float) -> float:
    if not values:
        return math.nan
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    low, high = int(math.floor(k)), int(math.ceil(k))
    return values[low] + (values[high] - values[low]) * (k - low)


def summarize(results: list[Result], wall: float) -> dict:
    ok = [r for r in results if r.ok]
    tpot = [(r.latency - r.ttft) / (r.completion_tokens - 1) * 1000 for r in ok if r.completion_tokens > 1]
    out_tokens = sum(r.completion_tokens for r in ok)
    in_tokens = sum(r.prompt_tokens for r in ok)
    mean = lambda xs: sum(xs) / len(xs) if xs else math.nan  # noqa: E731
    return {
        "ok": len(ok), "total": len(results),
        "prompt_tokens_avg": mean([r.prompt_tokens for r in ok]),
        "completion_tokens_avg": mean([r.completion_tokens for r in ok]),
        "ttft_p50": pct([r.ttft for r in ok], 50), "ttft_p95": pct([r.ttft for r in ok], 95),
        "latency_p50": pct([r.latency for r in ok], 50), "latency_p95": pct([r.latency for r in ok], 95),
        "tpot_ms": mean(tpot),
        "out_tok_s": out_tokens / wall, "total_tok_s": (in_tokens + out_tokens) / wall,
        "req_s": len(ok) / wall, "wall_s": wall,
    }


def run_level(args, headers, prompt_tokens: int, max_tokens: int, concurrency: int) -> tuple[dict, list[str]]:
    count = max(args.requests, concurrency)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one_request, args.url, headers, args.model, build_prompt(prompt_tokens),
                               max_tokens, not args.no_ignore_eos, args.timeout) for _ in range(count)]
        results = [f.result() for f in futures]
    wall = time.perf_counter() - started
    return summarize(results, wall), sorted({r.error for r in results if r.error})


def fmt(x: float, digits: int = 2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("LLM_API_URL", "http://localhost:8000/v1/chat/completions"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--concurrency", default="1,2,4", help="уровни параллелизма через запятую")
    parser.add_argument("--requests", type=int, default=8, help="запросов на уровень (минимум = параллелизм)")
    parser.add_argument("--short-tokens", type=int, default=16, help="размер короткого промпта, токенов")
    parser.add_argument("--long-tokens", type=int, default=1500, help="размер длинного промпта, токенов")
    parser.add_argument("--short-out", type=int, default=64, help="max_tokens для коротких")
    parser.add_argument("--long-out", type=int, default=64, help="max_tokens для длинных")
    parser.add_argument("--scenarios", default="short,long")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--no-ignore-eos", action="store_true")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    parser.add_argument("--label", default="baseline", help="метка прогона (имя оверлея/профиля)")
    args = parser.parse_args()

    token = get_token()
    if not token:
        print("Токен не найден (LLM_API_TOKEN или доступ kubectl к Secret).", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    presets = {"short": (args.short_tokens, args.short_out), "long": (args.long_tokens, args.long_out)}
    rows, report = [], []
    for name in [s.strip() for s in args.scenarios.split(",") if s.strip()]:
        prompt_tokens, max_tokens = presets[name]
        for _ in range(args.warmup):
            one_request(args.url, headers, args.model, build_prompt(prompt_tokens), min(max_tokens, 8),
                        True, args.timeout)
        for concurrency in [int(c) for c in args.concurrency.split(",")]:
            print(f"-> {name}, concurrency={concurrency} ...", file=sys.stderr, flush=True)
            summary, errors = run_level(args, headers, prompt_tokens, max_tokens, concurrency)
            summary.update(scenario=name, concurrency=concurrency, label=args.label)
            report.append(summary)
            rows.append(
                f"| {name} | {fmt(summary['prompt_tokens_avg'], 0)} | {fmt(summary['completion_tokens_avg'], 0)} "
                f"| {concurrency} | {summary['ok']}/{summary['total']} "
                f"| {fmt(summary['ttft_p50'])} | {fmt(summary['ttft_p95'])} "
                f"| {fmt(summary['latency_p50'])} | {fmt(summary['latency_p95'])} "
                f"| {fmt(summary['tpot_ms'], 1)} | {fmt(summary['out_tok_s'])} "
                f"| {fmt(summary['total_tok_s'])} | {fmt(summary['req_s'], 3)} |"
            )
            if errors:
                print(f"   ошибки: {errors}", file=sys.stderr)

    table = (
        f"Профиль: **{args.label}**\n\n"
        "| Сценарий | Prompt tok | Out tok | Параллелизм | OK/всего | TTFT p50, с | TTFT p95, с "
        "| Latency p50, с | Latency p95, с | TPOT, мс/ток | Out tok/s | Total tok/s | Req/s |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows)
    )
    print("\n" + table)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{args.label}-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / f"{args.label}-{stamp}.md").write_text(table + "\n", encoding="utf-8")
    print(f"\nСохранено в {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
