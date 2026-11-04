# CPU-инференс LLM (vLLM) в Kubernetes

`Qwen/Qwen2.5-1.5B-Instruct` на vLLM v0.27.0 (CPU) в Kubernetes. Веса скачивает отдельный rootless
initContainer в PVC (при рестартах сеть не используется), API защищён Bearer-токеном из k8s Secret,
в логах нет промптов и ответов.

## Требования
- Docker и `kubectl`; кластер: Docker Desktop (Kubernetes), Minikube или kind. Рекомендуется ~16–24 ГБ RAM для кластера.
- Python 3.10+ (клиент и скрипты без сторонних пакетов). На Linux/macOS команда `python3`.
- Windows / Linux / macOS. Различается только запись путей в `docker run -v` (ниже даны оба варианта).

## Структура
`docker/vllm` — образ vLLM, `loader` — образ загрузчика, `k8s/base` — манифесты (kustomize),
`k8s/overlays` — профили настройки, `scripts` — секрет и проверки, `loadtest` — нагрузочный тест.

## 1. Lock зависимостей vLLM-образа (один раз)
База закреплена по digest; полный список пакетов (с транзитивными) фиксируется в `docker/vllm/requirements.txt`,
а сборка проверяет, что он совпадает с образом. Сгенерируйте файл и закоммитьте:

```bash
# Linux / macOS
IMG=vllm/vllm-openai-cpu:v0.27.0@sha256:16981b40bb538e9127ad82e74a9f5386fddb4071c141725309d7ca4e2ea5872e
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)/docker/vllm:/work" --entrypoint python $IMG \
  /work/lock.py freeze --out /work/requirements.txt
```
```powershell
# Windows PowerShell
$IMG = "vllm/vllm-openai-cpu:v0.27.0@sha256:16981b40bb538e9127ad82e74a9f5386fddb4071c141725309d7ca4e2ea5872e"
docker run --rm -v "${PWD}/docker/vllm:/work" --entrypoint python $IMG /work/lock.py freeze --out /work/requirements.txt
```
Аудит уязвимостей: `pip-audit -r docker/vllm/requirements.txt` и `pip-audit -r loader/requirements.txt`.

## 2. Сборка образов
```bash
docker build -t llm-inference:local docker/vllm
docker build -t llm-model-loader:local loader
```
Доставка образов в кластер:
- Docker Desktop (Kubernetes): ничего не нужно.
- Minikube: `minikube image load llm-inference:local llm-model-loader:local`
- kind: `kind load docker-image llm-inference:local llm-model-loader:local`
- Реальный кластер: запушьте в реестр и поправьте `images:` в `k8s/base/kustomization.yaml`.

## 3. Secret (токены не печатаются, не попадают в историю и в файлы)
```bash
python scripts/create_secret.py                 # случайный API-токен
python scripts/create_secret.py --hf-token      # + HF-токен для gated-моделей (скрытый ввод)
python scripts/create_secret.py --rotate        # заменить API-токен (потом: kubectl rollout restart deployment/vllm -n llm-inference)
```

## 4. Деплой
```bash
kubectl apply -k k8s/base
kubectl rollout status deployment/vllm -n llm-inference --timeout=30m
kubectl logs -n llm-inference deployment/vllm -c download-model
kubectl port-forward -n llm-inference service/vllm 8000:8000
```
Параметры инференса меняются в `k8s/base/inference.env` и применяются повторным `kubectl apply -k k8s/base`:
имя ConfigMap содержит хэш, под пересоздаётся сам. Закрепите `MODEL_REVISION` на SHA, который напечатает загрузчик.

## 5. Запросы
```bash
kubectl port-forward -n llm-inference service/vllm 8000:8000     # оставить открытым
python ask.py "Ответь по-русски: что такое PVC?"
python ask.py --max-tokens 60 --system "Отвечай кратко" "Что такое initContainer?"
```
Токен клиент берёт из `LLM_API_TOKEN`, иначе из Secret через `kubectl`, иначе спрашивает скрытым вводом.
`.env`-файлов нет. Токен нигде не сохраняется.

## 6. Кэш весов
Первый запуск скачивает модель в PVC и пишет маркер `.download-complete`. При рестарте загрузчик видит маркер,
пишет `already present ... no network access` и завершается без сети (`huggingface_hub` даже не импортируется).
Основной контейнер дополнительно работает с `HF_HUB_OFFLINE=1`. Если после рестарта нужно снова принимать запросы с локальной машины, запустите port-forward в отдельном окне и оставьте его работающим.
```bash
kubectl rollout restart deployment/vllm -n llm-inference
kubectl rollout status deployment/vllm -n llm-inference --timeout=30m
kubectl logs -n llm-inference deployment/vllm -c download-model
kubectl port-forward -n llm-inference service/vllm 8000:8000
```

## 7. Приватные логи
- `request_audit.py`: чистый ASGI-middleware пишет `method path status ttfb_ms duration_ms client`. Тело, query и заголовки не читаются.
- `ContentRedactionFilter` (через `VLLM_LOGGING_CONFIG_PATH`) вырезает содержимое `vllm.entrypoints.logger` и трейсбеки.
- Access-log uvicorn выключен, `uvicorn-log-level=warning`, usage stats vLLM выключены.
- Пробы `/health` логируются на уровне DEBUG.

Проверка (с открытым port-forward):
```bash
python scripts/verify.py
```
Скрипт проверяет: 401 без токена, canary-промпт и начало ответа не в логах, токен не в логах и не в argv, факт запроса залогирован.

## 8. Нагрузочное тестирование (бонус 3)
```bash
python loadtest/loadtest.py --label baseline --concurrency 1,2,4 --requests 8
# другой профиль:
kubectl apply -k k8s/overlays/short-flood        # или long-context
kubectl rollout status deployment/vllm -n llm-inference
kubectl port-forward -n llm-inference service/vllm 8000:8000  # в отдельном окне; после смены профиля запустить заново
python loadtest/loadtest.py --label short-flood --scenarios short --concurrency 1,4,8,16 --requests 32
kubectl apply -k k8s/overlays/long-context
kubectl rollout status deployment/vllm -n llm-inference
kubectl port-forward -n llm-inference service/vllm 8000:8000  # если предыдущий port-forward завершился
python loadtest/loadtest.py --label long-context --scenarios long --long-tokens 6000 --concurrency 1,2 --requests 4
```
Метрики: **TTFT** — время до первого токена; **Latency** — полное время запроса; **TPOT** — мс на токен после первого;
**Throughput** — Out tok/s (выходные токены в секунду за всё время уровня), Total tok/s (вход + выход), Req/s.
Промпты уникальны (prefix cache не искажает), длина вывода фиксирована (`ignore_eos`).

### Результаты
Baseline: `MAX_MODEL_LEN=2048`, `MAX_NUM_SEQS=2`, `MAX_NUM_BATCHED_TOKENS=2048`, `VLLM_CPU_KVCACHE_SPACE=2`, `LLM_DTYPE=auto`. Прогон: `--concurrency 1,2,4 --requests 8`; два запроса long при concurrency 4 завершились timeout.

| Сценарий | Prompt tok | Out tok | Параллелизм | OK/всего | TTFT p50, с | TTFT p95, с | Latency p50, с | Latency p95, с | TPOT, мс/ток | Out tok/s | Total tok/s | Req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| short | 52 | 64 | 1 | 8/8 | 7.95 | 8.17 | 21.86 | 22.13 | 220.5 | 2.93 | 5.31 | 0.046 |
| short | 52 | 64 | 2 | 8/8 | 12.14 | 16.47 | 41.10 | 41.52 | 459.4 | 3.11 | 5.65 | 0.049 |
| short | 54 | 64 | 4 | 8/8 | 49.83 | 54.91 | 82.57 | 88.11 | 508.4 | 3.10 | 5.69 | 0.048 |

Профиль short-flood: `MAX_MODEL_LEN=1024`, `MAX_NUM_SEQS=16`, `MAX_NUM_BATCHED_TOKENS=2048`, `VLLM_CPU_KVCACHE_SPACE=4`; тест `--concurrency 1,4,8 --requests 4`.

| Сценарий | Prompt tok | Out tok | Параллелизм | OK/всего | TTFT p50, с | TTFT p95, с | Latency p50, с | Latency p95, с | TPOT, мс/ток | Out tok/s | Total tok/s | Req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| short-flood | 52 | 64 | 1 | 4/4 | 7.87 | 8.00 | 21.83 | 22.33 | 222.4 | 2.92 | 5.29 | 0.046 |
| short-flood | 53 | 64 | 4 | 4/4 | 32.91 | 32.91 | 81.31 | 81.32 | 865.4 | 3.15 | 5.74 | 0.049 |
| short-flood | 51 | 64 | 8 | 8/8 | 63.44 | 63.44 | 157.30 | 157.30 | 1596.9 | 3.25 | 5.85 | 0.051 |
| long | 1287 | 64 | 1 | 8/8 | 234.96 | 239.00 | 249.20 | 255.84 | 236.3 | 0.26 | 5.40 | 0.004 |
| long | 1289 | 64 | 2 | 8/8 | 363.54 | 502.50 | 501.96 | 544.13 | 2358.1 | 0.25 | 5.30 | 0.004 |
| long | 1289 | 64 | 4 | 6/8 | 744.57 | 798.60 | 924.72 | 1326.87 | 5060.3 | 0.18 | 3.89 | 0.003 |

Дополнительный профиль long-context: `MAX_MODEL_LEN=2048`, `MAX_NUM_SEQS=1`, `MAX_NUM_BATCHED_TOKENS=2048`, `VLLM_CPU_KVCACHE_SPACE=2`, `LLM_DTYPE=auto`; тест `--long-tokens 100 --concurrency 1,2 --requests 4`.

| Сценарий | Prompt tok | Out tok | Параллелизм | OK/всего | TTFT p50, с | TTFT p95, с | Latency p50, с | Latency p95, с | TPOT, мс/ток | Out tok/s | Total tok/s | Req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| long-context | 125 | 64 | 1 | 4/4 | 19.02 | 19.36 | 32.98 | 33.26 | 221.0 | 1.94 | 5.73 | 0.030 |
| long-context | 125 | 64 | 2 | 4/4 | 51.98 | 52.32 | 65.91 | 66.25 | 221.6 | 1.94 | 5.73 | 0.030 |

### Аналитика: что менять для throughput
Ниже рассуждения по документации vLLM и архитектуре CPU-инференса, а не измеренные выводы. Проверьте их оверлеями выше.

**А. Много запросов с малым числом токенов (`overlays/short-flood`).** Декод на CPU упирается в пропускную способность памяти:
на каждый шаг читаются все веса, поэтому чем больше последовательностей в батче, тем дешевле токен.
- Поднять `--max-num-seqs` (8–32, пока хватает ядер и RAM): основной рычаг.
- Поднять `--max-num-batched-tokens` (≥ `max-num-seqs` × средний промпт, 2048–4096), чтобы префилл коротких промптов не тормозил набор батча.
- Снизить `--max-model-len` до реально нужного (1024): меньше резерв KV, больше одновременных запросов.
- `VLLM_CPU_KVCACHE_SPACE` ≥ seqs × len × ~28 КиБ/токен (Qwen2.5-1.5B: 28 слоёв, 2 KV-головы, head_dim 128, bf16).
- `--enable-prefix-caching`, если у запросов общий системный промпт.
- Не превышать число физических ядер (`cpu limit` = число потоков, при необходимости `VLLM_CPU_OMP_THREADS_BIND`). Дальше масштабировать репликами.

**Б. Мало запросов, но огромный контекст (`overlays/long-context`).** Префилл упирается в вычисления (линейная часть и attention), KV-кэш одной последовательности большой.
- Поднять `--max-model-len` под реальный контекст (KV ≈ 28 КиБ × токены: 8192 ток ≈ 235 МБ на последовательность).
- Увеличить `--max-num-batched-tokens` (4096–8192): крупнее куски префилла, лучше загрузка ядер и выше throughput.
  Цена — рост TTFT и межтокенной задержки у параллельных запросов; при chunked prefill искать баланс.
- Снизить `--max-num-seqs` (2–4): каждая последовательность держит много KV, лишний параллелизм только вытесняет кэш.
- Увеличить `VLLM_CPU_KVCACHE_SPACE` и memory limit пода под seqs × контекст.
- `--enable-prefix-caching`, если большой префикс повторяется (RAG, один документ — много вопросов): TTFT падает кратно.

## 9. Безопасность
Rootless (UID/GID 10001), `runAsNonRoot`, seccomp `RuntimeDefault`, drop ALL, `allowPrivilegeEscalation: false`,
`readOnlyRootFilesystem`, namespace с Pod Security `restricted`, без ServiceAccount-токена, NetworkPolicy.
API-токен — только в Secret → переменная `VLLM_API_KEY` (не argv); `start.sh` не запускает сервер без неё.
HF-токен виден только initContainer. Код middleware запечён в образ, а не подмонтирован из ConfigMap.

## Траблшутинг
- Под в `CreateContainerConfigError`: не создан Secret (шаг 3).
- `Permission denied` на `/models`: провайдер PVC не применил `fsGroup`; на hostPath-провайдерах выдайте тому права 0777 или добавьте init-шаг `chown`.
- vLLM падает на записи в файловую систему: закомментируйте `readOnlyRootFilesystem` в `deployment.yaml`.
- Неизвестный флаг `--disable-uvicorn-access-log`: удалите его из `docker/vllm/start.sh`.
- Медленно или ошибка dtype на старом CPU: `LLM_DTYPE=float32` в `inference.env`.
