FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/django_bridge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend/django_bridge/ /app/
RUN mkdir -p /app/data && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8016
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn ramon_django.wsgi:application --bind 0.0.0.0:8016 --workers 2 --timeout 10"]
