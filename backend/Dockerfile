FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 \
      curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=false
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 app
COPY --chown=app:app . .
# These three are named-volume mount points. Docker seeds a fresh volume from
# the image directory underneath it, ownership included, so each one has to
# exist and belong to "app" here - otherwise the volume arrives owned by root
# and the unprivileged process cannot write to it. /var/lib/celery holds Beat's
# schedule, which must not live in /app: that is the git checkout.
RUN mkdir -p /app/staticfiles /app/media /var/lib/celery \
    && chown -R app:app /app/staticfiles /app/media /var/lib/celery
USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
