FROM python:3.11-slim
RUN pip install --no-cache-dir "paho-mqtt>=2,<3" "psutil>=5.9,<8" \
 && useradd --system --uid 10001 --no-create-home metrics
WORKDIR /app
COPY host_metrics.py .
USER metrics
CMD ["python", "-u", "host_metrics.py"]
