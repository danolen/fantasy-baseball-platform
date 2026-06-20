# Image for running Prefect flows on ECS/Fargate (Options B/C in
# docs/adr/0001-prefect-on-aws.md). Also a fine base for Prefect Managed.
#
# Build for linux/amd64 explicitly — Fargate rejects arm64 images unless the
# task is configured for it, and local Apple-silicon builds default to arm64:
#   docker build --platform linux/amd64 -t fbb-flows:latest flows/
FROM prefecthq/prefect:3-python3.12

WORKDIR /opt/flows

# Install deps first for better layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .
