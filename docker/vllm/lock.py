#!/usr/bin/env python3
"""Lock-файл Python-пакетов образа.

  python lock.py freeze [--out FILE]   вывести name==version для ВСЕХ установленных пакетов
  python lock.py check FILE            сверить установленное с lock-файлом (exit 1 при расхождении)
"""
import argparse
import re
import sys
from importlib import metadata

HEADER = (
    "# Автогенерация: python lock.py freeze\n"
    "# Все пакеты (включая транзитивные) закреплены; база образа закреплена по digest.\n"
)


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed() -> dict[str, str]:
    packages: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            packages[norm(name)] = dist.version
    return packages


def read_lock(path: str) -> dict[str, str]:
    pinned: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"([A-Za-z0-9_.\-]+)==(\S+)", line)
            if not match:
                sys.exit(f"Некорректная строка в {path}: {line!r} (разрешены только pin'ы ==)")
            pinned[norm(match[1])] = match[2]
    return pinned


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--out")
    check = sub.add_parser("check")
    check.add_argument("file")
    args = parser.parse_args()

    if args.cmd == "freeze":
        text = HEADER + "".join(f"{n}=={v}\n" for n, v in sorted(installed().items()))
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        else:
            sys.stdout.write(text)
        return 0

    pinned, have = read_lock(args.file), installed()
    if not pinned:
        print("requirements.txt пуст: сгенерируйте его командой из README (шаг 1).", file=sys.stderr)
        return 1
    problems = []
    for name, version in pinned.items():
        if name not in have:
            problems.append(f"нет в образе: {name}=={version}")
        elif have[name] != version:
            problems.append(f"версия {name}: lock={version}, образ={have[name]}")
    problems += [f"нет в lock-файле: {n}=={v}" for n, v in have.items() if n not in pinned]
    if problems:
        print("Lock-файл не соответствует образу:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"lock OK: {len(pinned)} пакетов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
