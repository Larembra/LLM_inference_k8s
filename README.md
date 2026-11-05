# CPU-инференс LLM (vLLM) в Kubernetes

`Qwen/Qwen2.5-1.5B-Instruct` на vLLM v0.27.0 (CPU) в Kubernetes. Веса скачивает отдельный rootless
initContainer в PVC (при рестартах сеть не используется), API защищён Bearer-токеном из k8s Secret,
в логах нет промптов и ответов.

## Требования
- Docker и `kubectl`; кластер: Docker Desktop (Kubernetes), Minikube или kind. Рекомендуется ~16–24 ГБ RAM для кластера.
- Python 3.10+ (клиент и скрипты без сторонних пакетов). На Linux/macOS команда `python3`.
- Windows / Linux / macOS. Команды приведены для PowerShell и bash.

## Проверенные среды и ограничения
- **Проверено:** Windows (PowerShell), Docker Desktop с kind-кластером (узел `desktop-control-plane`), x86_64.
  Сборка без кэша, развёртывание с нуля (пустой PVC, загрузка модели), `scripts/verify.py` (16 проверок), рестарт без сети.
- **Не проверялось:** Linux, macOS, Minikube, отдельный kind, arm64. Команды для них приведены по документации, но не запускались.
- Первый запрос после старта может занимать около двух минут (прогрев на CPU).
- Скорость генерации на CPU низкая (см. замеры в разделе 8).

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
Аудит уязвимостей (нужен `pip install pip-audit`):
```bash
pip-audit -r loader/requirements.txt
pip-audit -r docker/vllm/requirements.txt
```
Зависимости загрузчика (`loader/requirements.txt`) обновляются по результатам аудита. Зависимости образа vLLM
берутся из базового образа и меняются только его заменой (см. «Известные ограничения»).

## 2. Сборка образов
Меняйте тег при каждой пересборке: с `imagePullPolicy: IfNotPresent` кластер молча берёт образ со старым тегом.
```bash
docker build --no-cache -t llm-inference:r2 docker/vllm
docker build --no-cache -t llm-model-loader:r2 loader
```
Те же теги указаны в `images:` файла `k8s/base/kustomization.yaml`.

Доставка образов в кластер (если узел называется `desktop-control-plane`, это kind внутри Docker Desktop):
- Docker Desktop с kubeadm-кластером: ничего не нужно, образы общие с Docker.
- Docker Desktop с kind-кластером (узел `desktop-control-plane`): образы нужно загрузить в узел.
  ```powershell
  # Windows PowerShell
  docker save --platform linux/amd64 -o llm-images.tar llm-inference:r2 llm-model-loader:r2
  docker cp llm-images.tar desktop-control-plane:/llm-images.tar
  docker exec desktop-control-plane ctr -n k8s.io images import /llm-images.tar
  docker exec desktop-control-plane crictl images | Select-String "llm-"
  Remove-Item llm-images.tar
  docker exec desktop-control-plane rm -f /llm-images.tar
  ```
  ```bash
  # Linux / macOS
  docker save --platform linux/amd64 -o llm-images.tar llm-inference:r2 llm-model-loader:r2
  docker cp llm-images.tar desktop-control-plane:/llm-images.tar
  docker exec desktop-control-plane ctr -n k8s.io images import /llm-images.tar
  docker exec desktop-control-plane crictl images | grep llm-
  rm llm-images.tar
  docker exec desktop-control-plane rm -f /llm-images.tar
  ```
  Не передавайте вывод `docker save` через конвейер PowerShell: бинарные данные портятся.
- kind (отдельно установленный): `kind load docker-image llm-inference:r2 llm-model-loader:r2 --name <кластер>`
- Minikube: `minikube image load llm-inference:r2 llm-model-loader:r2`
- Реальный кластер: запушьте в реестр и поправьте `newName` в `images:` файла `k8s/base/kustomization.yaml`.

Проверить, что в поде запущены нужные образы:
```bash
kubectl get pod -n llm-inference -o jsonpath="{.items[0].spec.containers[*].image}"
kubectl get pod -n llm-inference -o jsonpath="{.items[0].spec.initContainers[*].image}"
```

## 3. Secret (токены не печатаются, не попадают в историю и в файлы)
```bash
python scripts/create_secret.py                 # случайный API-токен
python scripts/create_secret.py --hf-token      # + HF-токен для gated-моделей (скрытый ввод)
python scripts/create_secret.py --rotate        # заменить API-токен (потом: kubectl rollout restart deployment/vllm -n llm-inference)
```

## 4. Деплой
```bash
kubectl apply -k k8s/base
kubectl rollout status deployment/vllm -n llm-inference --timeout=60m
kubectl logs -n llm-inference deployment/vllm -c download-model
```
Первый запуск скачивает около 3 ГБ и загружает модель на CPU: это занимает 10–20 минут. Статус `Init:CrashLoopBackOff`
при сетевых сбоях Hugging Face штатный (init-контейнер перезапускается сам, скачивание продолжается).

Доступ к API с локальной машины (оставьте окно открытым):
```powershell
# Windows PowerShell: туннель с автоматическим перезапуском
while ($true) { kubectl port-forward -n llm-inference service/vllm 8000:8000; Start-Sleep -Seconds 2 }
```
```bash
# Linux / macOS
while true; do kubectl port-forward -n llm-inference service/vllm 8000:8000; sleep 2; done
```
Параметры инференса меняются в `k8s/base/inference.env` и применяются повторным `kubectl apply -k k8s/base`:
имя ConfigMap содержит хэш, под пересоздаётся сам. `MODEL_REVISION` закреплён на SHA коммита модели.

## 5. Запросы
```bash
python ask.py "Что такое PersistentVolumeClaim в Kubernetes? Ответь по-русски."
python ask.py --max-tokens 60 --system "Отвечай кратко" "Что такое initContainer?"
```
Токен клиент берёт из `LLM_API_TOKEN`, иначе из Secret через `kubectl`, иначе спрашивает скрытым вводом.
`.env`-файлов нет. Токен нигде не сохраняется. Для демонстрации лучше формулировать вопрос с контекстом:
без слова Kubernetes модель может расшифровать PVC как «поливинилхлорид».

## 6. Кэш весов
Первый запуск скачивает модель в PVC и пишет маркер `.download-complete`. При рестарте загрузчик видит маркер,
пишет `already present ... no network access` и завершается без сети (`huggingface_hub` даже не импортируется).
Основной контейнер дополнительно работает с `HF_HUB_OFFLINE=1`.
```bash
kubectl rollout restart deployment/vllm -n llm-inference
kubectl rollout status deployment/vllm -n llm-inference --timeout=30m
kubectl logs -n llm-inference deployment/vllm -c download-model
```
После рестарта `port-forward` оборвётся (если вы не используете цикл из шага 4): запустите его заново.

## 7. Приватные логи и защита путей
- `request_audit.py`: чистый ASGI-middleware пишет `method path status ttfb_ms duration_ms client`. Тело, query и заголовки не читаются.
- **Белый список путей:** снаружи доступны только `/health` (пробы) и `/v1/*` (токен проверяет vLLM). Всё остальное
  (`/invocations`, `/tokenize`, `/metrics`, `/docs`, `/openapi.json`, `/version` и др.) отвечает `404` ещё до vLLM.
  Без этого `/invocations` выполнял инференс без токена.
- `ContentRedactionFilter` (через `VLLM_LOGGING_CONFIG_PATH`) вырезает содержимое `vllm.entrypoints.logger` и трейсбеки.
- Access-log uvicorn выключен, `uvicorn-log-level=warning`, usage stats vLLM выключены.
- Пробы `/health` логируются на уровне DEBUG.

Проверка (с открытым port-forward):
```bash
python scripts/verify.py
```
Скрипт проверяет: без токена закрыты `/v1/*` (401) и служебные пути (404), `/health` открыт, canary-промпт (в успешном
запросе и в запросе с ошибкой) и начало ответа не в логах, токен не в логах и не в argv, факт запроса залогирован.

## 8. Нагрузочное тестирование (бонус 3)
Каждый профиль применяется отдельно; `port-forward` после смены профиля нужно запускать заново (или используйте цикл из шага 4).
```bash
# baseline (k8s/base)
kubectl apply -k k8s/base
kubectl rollout status deployment/vllm -n llm-inference --timeout=30m
python loadtest/loadtest.py --label baseline --concurrency 1,2,4 --requests 8

# профиль short-flood
kubectl apply -k k8s/overlays/short-flood
kubectl rollout status deployment/vllm -n llm-inference --timeout=30m
python loadtest/loadtest.py --label short-flood --scenarios short --concurrency 1,4,8 --requests 4

# профиль long-context (MAX_MODEL_LEN=2048: длинный промпт не должен превышать лимит с запасом на ответ)
kubectl apply -k k8s/overlays/long-context
kubectl rollout status deployment/vllm -n llm-inference --timeout=30m
python loadtest/loadtest.py --label long-context --scenarios long --long-tokens 1500 --concurrency 1,2 --requests 4

# вернуть базовый профиль
kubectl apply -k k8s/base
```
Метрики: **TTFT** — время до первого токена; **Latency** — полное время запроса; **TPOT** — мс на токен после первого;
**Throughput** — Out tok/s (выходные токены в секунду за всё время уровня), Total tok/s (вход + выход), Req/s.
Промпты уникальны (prefix cache не искажает), длина вывода фиксирована (`ignore_eos`).

### Результаты
Железо: Docker Desktop (kind), лимиты пода `cpu: 6`, `memory: 16Gi`, `LLM_DTYPE=auto`. Все значения измерены.

**Baseline:** `MAX_MODEL_LEN=2048`, `MAX_NUM_SEQS=2`, `MAX_NUM_BATCHED_TOKENS=2048`, `VLLM_CPU_KVCACHE_SPACE=2`.
Прогон: `--concurrency 1,2,4 --requests 8`; два запроса long при параллелизме 4 завершились по таймауту.

| Сценарий | Prompt tok | Out tok | Параллелизм | OK/всего | TTFT p50, с | TTFT p95, с | Latency p50, с | Latency p95, с | TPOT, мс/ток | Out tok/s | Total tok/s | Req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| short | 52 | 64 | 1 | 8/8 | 7.95 | 8.17 | 21.86 | 22.13 | 220.5 | 2.93 | 5.31 | 0.046 |
| short | 52 | 64 | 2 | 8/8 | 12.14 | 16.47 | 41.10 | 41.52 | 459.4 | 3.11 | 5.65 | 0.049 |
| short | 54 | 64 | 4 | 8/8 | 49.83 | 54.91 | 82.57 | 88.11 | 508.4 | 3.10 | 5.69 | 0.048 |
| long | 1287 | 64 | 1 | 8/8 | 234.96 | 239.00 | 249.20 | 255.84 | 236.3 | 0.26 | 5.40 | 0.004 |
| long | 1289 | 64 | 2 | 8/8 | 363.54 | 502.50 | 501.96 | 544.13 | 2358.1 | 0.25 | 5.30 | 0.004 |
| long | 1289 | 64 | 4 | 6/8 | 744.57 | 798.60 | 924.72 | 1326.87 | 5060.3 | 0.18 | 3.89 | 0.003 |

**Профиль short-flood:** `MAX_MODEL_LEN=1024`, `MAX_NUM_SEQS=16`, `MAX_NUM_BATCHED_TOKENS=2048`, `VLLM_CPU_KVCACHE_SPACE=4`;
тест `--concurrency 1,4,8 --requests 4`.

| Сценарий | Prompt tok | Out tok | Параллелизм | OK/всего | TTFT p50, с | TTFT p95, с | Latency p50, с | Latency p95, с | TPOT, мс/ток | Out tok/s | Total tok/s | Req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| short-flood | 52 | 64 | 1 | 4/4 | 7.87 | 8.00 | 21.83 | 22.33 | 222.4 | 2.92 | 5.29 | 0.046 |
| short-flood | 53 | 64 | 4 | 4/4 | 32.91 | 32.91 | 81.31 | 81.32 | 865.4 | 3.15 | 5.74 | 0.049 |
| short-flood | 51 | 64 | 8 | 8/8 | 63.44 | 63.44 | 157.30 | 157.30 | 1596.9 | 3.25 | 5.85 | 0.051 |

**Профиль long-context:** `MAX_MODEL_LEN=2048`, `MAX_NUM_SEQS=1`, `MAX_NUM_BATCHED_TOKENS=2048`, `VLLM_CPU_KVCACHE_SPACE=2`.
Внимание: этот замер выполнен с `--long-tokens 100` (фактически 125 токенов), то есть он **не отражает длинный контекст**;
он показывает только эффект `MAX_NUM_SEQS=1` (запросы выстраиваются в очередь). Длинный контекст в этом профиле
измерен в baseline (1287 токенов).

| Сценарий | Prompt tok | Out tok | Параллелизм | OK/всего | TTFT p50, с | TTFT p95, с | Latency p50, с | Latency p95, с | TPOT, мс/ток | Out tok/s | Total tok/s | Req/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| long-context | 125 | 64 | 1 | 4/4 | 19.02 | 19.36 | 32.98 | 33.26 | 221.0 | 1.94 | 5.73 | 0.030 |
| long-context | 125 | 64 | 2 | 4/4 | 51.98 | 52.32 | 65.91 | 66.25 | 221.6 | 1.94 | 5.73 | 0.030 |

### Наблюдения (по измерениям)
- **Пропускная способность почти не растёт с параллелизмом:** Out tok/s около 2,9–3,3 при любом числе одновременных запросов
  (baseline: 2,93 → 3,10 при четырёх; short-flood: 2,92 → 3,25 при восьми). Рост `MAX_NUM_SEQS` с 2 до 16 дал около 11 %.
- **TPOT растёт почти линейно с параллелизмом** (222 мс → 865 мс при четырёх и 1597 мс при восьми запросах в short-flood):
  запросы фактически обрабатываются по очереди.
- **Префилл длинного промпта очень медленный:** 1287 токенов обработаны за 235 с (около 5,5 ток/с), то есть со скоростью
  порядка скорости декода. Для CPU-режима префилл обычно заметно быстрее декода.
- При четырёх длинных запросах два завершились по таймауту (900 с по умолчанию).

### Гипотезы (не проверены)
Причина низкой скорости не выяснена. Версию «упираемся в вычисления, а не в память» подтверждают наблюдения выше.
Что проверить: поддержку `avx512_bf16` / `amx_bf16` процессором (`grep -o -E "avx512_bf16|amx_bf16" /proc/cpuinfo` в контейнере),
переключение `LLM_DTYPE=float32`, привязку потоков (`VLLM_CPU_OMP_THREADS_BIND`) под `cpu limit` пода.

### Аналитика: что менять для throughput
Ниже рассуждения по документации vLLM и архитектуре CPU-инференса. На данном железе **гипотеза А не подтвердилась
измерениями** (см. выше), гипотеза Б не проверена вовсе.

**А. Много запросов с малым числом токенов (`overlays/short-flood`).** Ожидание: декод на CPU упирается в пропускную
способность памяти, поэтому большие батчи должны удешевлять токен.
- Поднять `--max-num-seqs` (8–32, пока хватает ядер и RAM). Измерено: прирост всего около 11 %.
- Поднять `--max-num-batched-tokens` (≥ `max-num-seqs` × средний промпт, 2048–4096), чтобы префилл коротких промптов не тормозил набор батча.
- Снизить `--max-model-len` до реально нужного (1024): меньше резерв KV, больше одновременных запросов.
- `VLLM_CPU_KVCACHE_SPACE` ≥ seqs × len × ~28 КиБ/токен (Qwen2.5-1.5B: 28 слоёв, 2 KV-головы, head_dim 128, bf16).
- `--enable-prefix-caching`, если у запросов общий системный промпт.
- Не превышать число физических ядер (`cpu limit` = число потоков, при необходимости `VLLM_CPU_OMP_THREADS_BIND`). Дальше масштабировать репликами.

**Б. Мало запросов, но огромный контекст (`overlays/long-context`).** Ожидание: префилл упирается в вычисления,
KV-кэш одной последовательности большой.
- Поднять `--max-model-len` под реальный контекст (KV ≈ 28 КиБ × токены: 8192 ток ≈ 235 МБ на последовательность).
- Увеличить `--max-num-batched-tokens` (4096–8192): крупнее куски префилла, лучше загрузка ядер.
  Цена — рост TTFT и межтокенной задержки у параллельных запросов; при chunked prefill искать баланс.
- Снизить `--max-num-seqs` (2–4): каждая последовательность держит много KV, лишний параллелизм только вытесняет кэш.
- Увеличить `VLLM_CPU_KVCACHE_SPACE` и memory limit пода под seqs × контекст.
- `--enable-prefix-caching`, если большой префикс повторяется (RAG, один документ — много вопросов).

## 9. Безопасность
Rootless (UID/GID 10001), `runAsNonRoot`, seccomp `RuntimeDefault`, drop ALL, `allowPrivilegeEscalation: false`,
`readOnlyRootFilesystem`, namespace с Pod Security `restricted`, без ServiceAccount-токена, NetworkPolicy.
API-токен — только в Secret → переменная `VLLM_API_KEY` (не argv); `start.sh` не запускает сервер без неё.
Снаружи открыты только `/health` и `/v1/*` (см. раздел 7). HF-токен виден только initContainer.
Код middleware запечён в образ, а не подмонтирован из ConfigMap. Веса закреплены SHA коммита модели
(`MODEL_REVISION`), базовые образы — digest'ами, зависимости — lock-файлами.

### Известные ограничения
- Пакеты базового образа `vllm/vllm-openai-cpu:v0.27.0` (`docker/vllm/requirements.txt`) меняются только заменой образа.
  Если `pip-audit` находит в них уязвимости, это ограничение базового образа. Контейнер vLLM не ходит за весами в сеть
  (`HF_HUB_OFFLINE=1`) и принимает запросы только на `/health` и `/v1/*`.
- `NetworkPolicy` на Docker Desktop может не применяться (зависит от CNI).
- Один общий API-токен на всех клиентов, TLS внутри кластера не настроен (доступ через `port-forward`).

## Траблшутинг
- Под в `CreateContainerConfigError`: не создан Secret (шаг 3).
- Под не создаётся, `kubectl get pods` пуст: смотрите `kubectl get events -n llm-inference --sort-by=.lastTimestamp`.
  Ошибка `violates PodSecurity "restricted"` означает, что у контейнера не хватает полей `securityContext`
  (`allowPrivilegeEscalation: false`, `capabilities.drop: ["ALL"]`); проверьте `k8s/base/deployment.yaml`.
- `ErrImagePull` / `ImagePullBackOff`: образ с таким тегом не загружен в узел (шаг 2).
- Запускается старый код под тем же тегом: пересобирайте под новым тегом и меняйте `newTag` в `kustomization.yaml`;
  сверьте `docker image inspect <образ> --format "{{.Id}}"` с `imageID` пода.
- `Evicted ... Usage of EmptyDir volume "tmp" exceeds the limit`: переполнен временный том. В манифесте у загрузчика
  отдельный `loader-tmp` (8 ГБ), у vLLM `tmp` (6 ГБ); при больших моделях увеличьте `sizeLimit`.
- `Init:CrashLoopBackOff` у `download-model` и `SSLEOFError` / `Read timed out` в логе: сетевой сбой до Hugging Face.
  Init-контейнер перезапускается сам, скачивание возобновляется; если не проходит, проверьте сеть, VPN и прокси.
- `kubectl port-forward` рвётся (`lost connection to pod`), и `verify.py` показывает `HTTP 0`: перезапустите туннель
  (цикл из шага 4 делает это автоматически).
- `Permission denied` на `/models`: провайдер PVC не применил `fsGroup`; на hostPath-провайдерах выдайте тому права 0777 или добавьте init-шаг `chown`.
- vLLM падает на записи в файловую систему: закомментируйте `readOnlyRootFilesystem` в `deployment.yaml`.
- Неизвестный флаг `--disable-uvicorn-access-log`: удалите его из `docker/vllm/start.sh`.
- Медленно или ошибка dtype на старом CPU: `LLM_DTYPE=float32` в `inference.env`.