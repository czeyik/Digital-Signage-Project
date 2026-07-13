FROM python:3.12-alpine3.23

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache ffmpeg clamav freshclam \
    && sed -i 's|^NotifyClamd |# NotifyClamd |' /etc/clamav/freshclam.conf

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir --upgrade pip setuptools==80.9.0 \
    && pip install --no-cache-dir . \
    && DJANGO_DEBUG=false \
       DJANGO_SECRET_KEY=container-build-only-secret-that-is-never-used-at-runtime \
       python manage.py collectstatic --noinput \
    && pip uninstall -y setuptools

RUN adduser -D -u 10001 appuser \
    && chown -R appuser:appuser /app /var/lib/clamav
USER appuser

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60"]
