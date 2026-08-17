FROM python:3.12-alpine3.23@sha256:601d3d3797e90e2534782e69c85fafb7971b43f24c7b1b079b7e48dd435e458d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk upgrade --no-cache \
    && apk add --no-cache ffmpeg clamav freshclam postgresql16-client su-exec \
    && sed -i 's|^NotifyClamd |# NotifyClamd |' /etc/clamav/freshclam.conf

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir --upgrade pip==26.2 setuptools==83.0.0 \
    && pip install --no-cache-dir . \
    && DJANGO_DEBUG=false \
       DEPLOYMENT_ENV=production \
       DJANGO_SECRET_KEY=container-build-only-secret-that-is-never-used-at-runtime \
       python manage.py collectstatic --noinput \
    && python -m pip uninstall -y setuptools pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip

RUN adduser -D -u 10001 appuser \
    && chown -R appuser:appuser /app /var/lib/clamav
USER appuser

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60"]
