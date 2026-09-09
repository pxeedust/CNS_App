FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 app
COPY --chown=app:app manage.py ./
COPY --chown=app:app cns_app/ cns_app/
COPY --chown=app:app outreach/ outreach/
RUN chown app:app /app
USER app
# No production credentials or database are needed to collect static assets.
RUN DJANGO_ENV=production DEBUG=False \
    SECRET_KEY=build-only-placeholder-not-a-runtime-secret-0000000000000000000000 \
    MAILBOX_ENCRYPTION_KEY=build-only-mailbox-placeholder \
    ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["gunicorn", "cns_app.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
