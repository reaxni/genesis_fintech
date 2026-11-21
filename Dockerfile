ARG PYTHON_IMAGE=python:3.11.11-slim-bookworm

FROM ${PYTHON_IMAGE} AS verify
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY . .
CMD ["python", "-m", "genesis_audit.runner.verify", "--episodes", "/app/episodes", "--output", "/out", "--socket", "/ipc/genesis.sock"]

FROM ${PYTHON_IMAGE} AS policy
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY src/genesis_audit/__init__.py /app/src/genesis_audit/__init__.py
COPY src/genesis_audit/contracts/__init__.py src/genesis_audit/contracts/v1.py src/genesis_audit/contracts/wire.py /app/src/genesis_audit/contracts/
COPY src/genesis_audit/policies/ /app/src/genesis_audit/policies/
COPY src/genesis_audit/runner/policy_worker.py /app/policy_worker.py
USER 65532:65532
CMD ["python", "/app/policy_worker.py", "/ipc/genesis.sock"]
