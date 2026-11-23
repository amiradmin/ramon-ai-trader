FROM ramon-django:latest
# Reuse the existing built Django image; do not rebuild or alter live services.
COPY --chown=10001:10001 lab/ /app/lab/
WORKDIR /app
USER 10001:10001
ENTRYPOINT ["python", "lab/run.py"]
