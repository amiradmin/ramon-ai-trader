FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/ramon/.cache/huggingface

WORKDIR /app

ARG RAMON_UID=1000
ARG RAMON_GID=1000

# Install CPU-only PyTorch first so pip does not fetch CUDA runtime packages.
RUN python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch>=2.2,<3'

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir '.[model,train]' \
    && groupadd --gid "$RAMON_GID" ramon \
    && useradd --create-home --uid "$RAMON_UID" --gid "$RAMON_GID" ramon \
    && mkdir -p /home/ramon/.cache/huggingface /checkpoints \
    && chown -R ramon:ramon /home/ramon /checkpoints

USER ramon

EXPOSE 8012
CMD ["python", "-m", "ramon.server", "--host", "0.0.0.0", "--device", "cpu"]
