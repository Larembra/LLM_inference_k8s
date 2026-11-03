# Инференс LLM на CPU в Kubernetes

Проект разворачивает `Qwen/Qwen2.5-1.5B-Instruct` через vLLM в локальном Kubernetes-кластере Docker Desktop. Init-контейнер загружает файлы модели в PVC. При пересоздании пода сохранённые файлы используются повторно. vLLM предоставляет API, совместимый с OpenAI, и требует Bearer-токен.

## Что нужно

- Docker Desktop с включённым Kubernetes и узлом в состоянии `Ready`
- `kubectl`
- Рекомендуется не менее 20 ГБ свободного места для Docker и 24 ГБ оперативной памяти

## Сборка образов

Выполняйте команды из корня проекта в PowerShell:

```powershell
docker build --pull -t llm-inference:local .
docker build --pull -t llm-model-loader:local .\loader
```

Первый образ основан на CPU-сборке vLLM версии `0.27.0`, второй — отдельный rootless-образ загрузчика модели. Если сборка прерывается с `unexpected EOF` при скачивании слоя, повторите команду. Ошибка в предоставленном логе означает, что Docker получил неполный слой базового образа; это сбой загрузки/распаковки, а не ошибка Dockerfile. Если повтор не поможет, перезапустите Docker Desktop и проверьте свободное место в Docker disk image.

## Создание Secret и развёртывание

Для публичной модели токен Hugging Face обычно не нужен. API-токен обязателен. Создайте Secret:

```powershell
$apiToken = Read-Host "Введите API-токен"
kubectl create namespace llm-inference
kubectl create secret generic inference-secrets -n llm-inference --from-literal=api-token=$apiToken
```

Если для доступа к модели понадобится Hugging Face токен, создайте Secret с обоими значениями:

```powershell
$apiToken = Read-Host "Введите API-токен"
$hfToken = Read-Host "Введите токен Hugging Face"
kubectl create secret generic inference-secrets -n llm-inference --from-literal=api-token=$apiToken --from-literal=hf-token=$hfToken
```

Затем примените манифесты:

```powershell
kubectl apply -k .\k8s
kubectl rollout status deployment/vllm -n llm-inference --timeout=30m
kubectl port-forward -n llm-inference service/vllm 8000:8000
```

При первом запуске init-контейнер скачает модель в PVC. Посмотреть его журнал можно командой:

```powershell
kubectl logs -n llm-inference deployment/vllm -c download-model
```

## Запрос к модели

Оставьте `port-forward` работать и откройте второе окно PowerShell:

```powershell
$apiToken = Read-Host "Введите API-токен"
$headers = @{ Authorization = "Bearer $apiToken" }
$body = @{ model = "Qwen/Qwen2.5-1.5B-Instruct"; messages = @(@{ role = "user"; content = "Объясни Kubernetes одним предложением." }); max_tokens = 100 } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri http://localhost:8000/v1/chat/completions -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

Логирование запросов vLLM отключено, чтобы промпты и ответы модели не попадали в stdout/stderr. События Kubernetes о состоянии пода при этом остаются доступны.

## Изменение конфигурации

Модель и основные параметры запуска задаются в `k8s/storage.yaml`. После изменения примените конфигурацию и перезапустите Deployment:

```powershell
kubectl apply -k .\k8s
kubectl rollout restart deployment/vllm -n llm-inference
```

Зависимости vLLM поставляются в версии-фиксированном базовом образе. Не устанавливайте поверх него другую незакреплённую версию vLLM.
