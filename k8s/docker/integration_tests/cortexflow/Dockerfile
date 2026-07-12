FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY pyproject.toml ./
COPY cortexflow/ ./cortexflow/
COPY tests/__init__.py ./tests/
COPY tests/integration/__init__.py ./tests/integration/
COPY tests/integration/stubs/ ./tests/integration/stubs/
COPY tests/integration/cortexflow/ ./tests/integration/cortexflow/

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked

# Install a first-party fixture package the way model-gateway reaches a
# consumer: git-installed into site-packages (VCS direct_url), NOT in-tree
# source. The remote-job bundler must trace it correctly and ship it whole.
# Kept out of /app so it is only reachable as an installed dependency.
COPY tests/integration/fixtures/ /fixtures/
RUN cd /fixtures/remote_fixture_pkg \
    && git init -q \
    && git add -A \
    && git -c user.email=ci@robolab.local -c user.name=ci commit -qm fixture \
    && uv pip install --python /app/.venv/bin/python "git+file:///fixtures/remote_fixture_pkg"

CMD ["/app/.venv/bin/python", "-m", "unittest", "discover", "-t", "/app", "-s", "tests", "-p", "test_*.py", "-v"]
