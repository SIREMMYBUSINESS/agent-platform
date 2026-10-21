FROM python:3.13.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
RUN if ! getent group 1000 >/dev/null; then groupadd --gid 1000 render-secrets; fi && \
    groupadd --system --gid 10001 app && \
    useradd --system --uid 10001 --gid app --home-dir /app app && \
    usermod --append --groups 1000 app

COPY pyproject.toml requirements.txt alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY ops/staging/start.sh ./ops/staging/start.sh
RUN python -m pip install --no-cache-dir -r requirements.txt .

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()" || exit 1
CMD ["python", "-m", "app.runtime_entry", "api"]
