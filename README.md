# CPU инференс LLM в Kubernetes

Проект запускает `Qwen/Qwen2.5-1.5B-Instruct` через vLLM в Kubernetes. Веса при первом запуске скачивает отдельный rootless initContainer в PVC, а API vLLM требует Bearer-токен. Запросы журналируются без содержимого промптов и ответов.

## Требования

- Docker Desktop с включённым Kubernetes; узел должен иметь статус `Ready`
- `kubectl`
- Docker Desktop с WSL 2; для модели на CPU рекомендуется выделить кластеру около 24 ГБ RAM
- PowerShell для приведённых ниже команд; CLI-клиент `ask.py` требует Python 3.10 или новее и сторонних пакетов не использует

## Зафиксированные зависимости

CPU-образ vLLM основан на `vllm/vllm-openai-cpu:v0.27.0`, закреплённом по digest в `Dockerfile`. Его Python runtime и транзитивные пакеты включены в неизменяемый образ.

Образ загрузчика основан на `python:3.12.10-slim-bookworm`, также закреплённом по digest в `loader/Dockerfile`. `loader/requirements.txt` закрепляет `huggingface_hub` и все его runtime-зависимости. Корневой `requirements.txt` содержит такую же копию списка. После изменения списка синхронизируйте оба файла.

## Сборка образов

Из корня проекта:

```powershell
docker build --pull -t llm-inference:local .
docker build --pull -t llm-model-loader:local .\loader
```

После изменения образа vLLM перезапустите Deployment, чтобы pod использовал новую сборку. Для изменения кода audit middleware пересборка vLLM образа не требуется: файл подключается ConfigMap из `k8s`.

## Secret и развёртывание

Публичной модели Hugging Face токен не нужен. Создайте случайный API-токен и Kubernetes Secret. Если namespace уже существует, пропустите команду `create namespace`.

```powershell
$apiToken = Read-Host "API token"
kubectl create namespace llm-inference
kubectl create secret generic inference-secrets -n llm-inference --from-literal=api-token=$apiToken
```

Если используете gated-модель, включите HF token в Secret:

```powershell
$apiToken = Read-Host "API token"
$hfToken = Read-Host "Hugging Face token"
kubectl create secret generic inference-secrets -n llm-inference --from-literal=api-token=$apiToken --from-literal=hf-token=$hfToken
```

Примените Kustomize-конфигурацию и дождитесь готовности:

```powershell
kubectl apply -k .\k8s
kubectl rollout status deployment/vllm -n llm-inference --timeout=30m
```

При первом запуске initContainer скачает веса в PVC объёмом 10 GiB. При рестартах существующие файлы переиспользуются. Посмотреть лог загрузчика можно так:

```powershell
kubectl logs -n llm-inference deployment/vllm -c download-model
```

## Запросы к модели

В первом окне PowerShell запустите проброс порта и оставьте его открытым:

```powershell
kubectl port-forward -n llm-inference service/vllm 8000:8000
```

Во втором окне один раз задайте токен для CLI. Команда запросит значение и сохранит его в локальный `.env`, исключённый из Git:

```powershell
python .\ask.py set-token
```

Задавайте вопросы, не собирая вручную JSON:

```powershell
python .\ask.py Ответь только по-русски: объясни Kubernetes одним предложением
python .\ask.py --max-tokens 60 Что такое PVC?
```

CLI читает `LLM_API_TOKEN` из переменной окружения, если она задана, иначе — из `.env`. Файл не попадает в Docker-образы. Для прямой проверки API можно использовать `/v1/models` с заголовком Bearer.

## Логи запросов

Audit middleware пишет метод, путь, HTTP-статус и длительность запроса. Оно не читает тело запроса или ответа и не пишет заголовки авторизации. Запросы `/health` также попадают в журнал.

```powershell
kubectl logs -n llm-inference deployment/vllm -c vllm --follow
```

Пример строки для вызова модели:

```text
request-audit method=POST path=/v1/chat/completions status=200 duration_ms=...
```

## Конфигурация

ID модели, путь и ограничения контекста задаются в `k8s/storage.yaml`. Сейчас контекст ограничен 512 токенами, одновременная последовательность — одна; параметры подобраны для запуска на локальном CPU с ограниченной RAM. После изменения примените конфигурацию:

```powershell
kubectl apply -k .\k8s
```

Изменение `ConfigMap` inference-config само по себе может не пересоздать pod. Для применения параметров запуска перезапустите Deployment:

```powershell
kubectl rollout restart deployment/vllm -n llm-inference
```
