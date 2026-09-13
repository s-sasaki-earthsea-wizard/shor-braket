# syntax=docker/dockerfile:1

# Pin the multi-platform Python image for reproducible ARM64 and AMD64 builds.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/tmp/numba-cache \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /workspace

COPY docker/requirements.txt docker/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r docker/requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN python -m pip install --no-deps --no-build-isolation --editable .

CMD ["shor-braket", "smoke"]
