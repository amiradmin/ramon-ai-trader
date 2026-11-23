FROM ramon-django:latest
# Reuse the existing built Django image; do not rebuild or alter live services.
COPY --chown=10001:10001 lab/ /app/lab/
WORKDIR /app
# Running /app/lab/run.py places /app/lab, not /app, on sys.path.
# Explicitly expose the existing Django project without touching live images.
ENV PYTHONPATH=/app:/app/lab
USER 10001:10001
ENTRYPOINT ["python", "lab/run.py"]
