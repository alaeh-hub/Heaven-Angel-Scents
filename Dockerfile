# Stage 1: build the React partner portal into static/public-site/
FROM node:22-alpine AS portal
WORKDIR /build
COPY public-site/package.json public-site/package-lock.json public-site/
RUN cd public-site && npm ci
COPY public-site public-site
RUN cd public-site && npm run build

# Stage 2: Flask app under gunicorn + gevent-websocket (single worker, see DEPLOY.md)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Run as an unprivileged user so a compromised app can't act as root in the container.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=app:app . .
COPY --from=portal --chown=app:app /build/static/public-site static/public-site
# Pre-create (and own) the uploads dir so the named volume mounted here inherits
# the right owner on first use.
RUN mkdir -p static/uploads && chown app:app static/uploads
USER app

EXPOSE 8000
# /healthz checks the app and the database. The X-Forwarded-Proto header keeps
# Talisman's HTTPS redirect from answering the plain-HTTP probe with a 301.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3   CMD python -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8000/healthz', headers={'X-Forwarded-Proto': 'https'}), timeout=4)" || exit 1
CMD ["gunicorn", "-k", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", "-w", "1", "wsgi:app", "--bind", "0.0.0.0:8000", "--timeout", "60", "--graceful-timeout", "30", "--access-logfile", "-"]
