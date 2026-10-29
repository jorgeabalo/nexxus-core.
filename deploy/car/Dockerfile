FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-car.txt .
RUN pip install --no-cache-dir -r requirements-car.txt
COPY car ./car
RUN useradd --uid 10001 --create-home aita && mkdir /data && chown aita /data
COPY deploy/car/entrypoint.sh /usr/local/bin/aita-entrypoint
RUN chmod 755 /usr/local/bin/aita-entrypoint
ENTRYPOINT ["aita-entrypoint"]
ENV AITA_CAR_DATABASE=/data/aita-car.sqlite
CMD ["sh", "-c", "uvicorn car.production:create_production --factory --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-access-log"]
