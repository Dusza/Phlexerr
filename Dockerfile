FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CONFIG_DIR=/config
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 99:100 = nobody:users, the Unraid default owner of appdata
RUN mkdir -p /config && chown 99:100 /config
USER 99:100
VOLUME /config
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" || exit 1

# One worker on purpose: report cache and refresh status live in memory.
CMD ["gunicorn", "-w", "1", "--threads", "8", "--timeout", "120", "-b", "0.0.0.0:8080", "app.main:app"]
