FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2 and pandas
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default: run the Slack bot (overridden for pipelines in ECS task defs)
CMD ["python", "-m", "src.slack.app"]
