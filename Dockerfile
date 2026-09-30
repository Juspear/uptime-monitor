FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY uptime_monitor ./uptime_monitor

# run as a non-root user
RUN useradd --create-home monitor && mkdir /app/data && chown monitor /app/data
USER monitor

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "uptime_monitor", "--config", "/app/config.toml"]
