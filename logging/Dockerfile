FROM python:3.11-slim
RUN pip install --no-cache-dir "psycopg[binary]>=3.2,<4"
WORKDIR /app
COPY logging/research_ingest.py /app/research_ingest.py
COPY logging/schema.sql /app/schema.sql
USER 65534:65534
CMD ["python", "-u", "/app/research_ingest.py"]
