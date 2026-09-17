#!/bin/sh
set -e

echo "Ожидание PostgreSQL..."
until python -c "
import os, sys, psycopg
try:
    psycopg.connect(os.environ['DATABASE_URL']).close()
except Exception as e:
    sys.exit(1)
" 2>/dev/null; do
    sleep 1
done
echo "PostgreSQL готов."

echo "Инициализация схемы и загрузка вердиктов..."
python -m scripts.06_seed_db

echo "Запуск приложения на :8080"
exec python -m app.main
