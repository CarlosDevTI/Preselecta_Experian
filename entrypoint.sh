#!/bin/sh
set -e

echo "Waiting for database at ${DB_HOST:-db}:${DB_PORT:-5432}..."
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-postgres}"; do
  sleep 1
done

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn preselecta_web.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
