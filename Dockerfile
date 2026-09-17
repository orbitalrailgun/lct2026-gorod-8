# Образ приложения. Витрины и сырые данные внутрь НЕ кладутся: приложение читает
# вердикты из PostgreSQL, поэтому образ остаётся лёгким, а `docker compose up`
# поднимается за секунды. Пересчёт вердиктов — отдельный офлайн-шаг на машине
# с данными (scripts/05_generate_verdicts.py).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости отдельным слоем: правка кода не тянет переустановку пакетов.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY deploy/seed_verdicts.json ./deploy/
COPY deploy/seed_geometry.json ./deploy/
COPY deploy/entrypoint.sh ./deploy/
RUN chmod +x ./deploy/entrypoint.sh

EXPOSE 8080

# Проверка живости для оркестратора и для эксперта, разворачивающего решение.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/api/health')"

ENTRYPOINT ["./deploy/entrypoint.sh"]
