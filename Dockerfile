# syntax=docker/dockerfile:1.7
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/ramon/.cache/huggingface \
    PIP_DEFAULT_TIMEOUT=600 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

ARG RAMON_UID=1000
ARG RAMON_GID=1000

# Install CPU-only PyTorch first so pip does not fetch CUDA runtime packages.
# Keep pip's download cache in a BuildKit cache mount so transient network
# failures do not force large packages to be downloaded from scratch.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
      --timeout 600 \
      --retries 10 \
      --index-url https://download.pytorch.org/whl/cpu \
      'torch>=2.2,<3'

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
      --timeout 600 \
      --retries 10 \
      '.[model,train]' \
    && groupadd --gid "$RAMON_GID" ramon \
    && useradd --create-home --uid "$RAMON_UID" --gid "$RAMON_GID" ramon \
    && mkdir -p /home/ramon/.cache/huggingface /checkpoints \
    && chown -R ramon:ramon /home/ramon /checkpoints

USER ramon

EXPOSE 8012
CMD ["python", "-m", "ramon.server", "--host", "0.0.0.0", "--device", "cpu"]
