# Chef — API + worker image. Same image runs `chef serve` and `chef worker`.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is handy for container healthchecks / debugging; git backs release resolution
# (chef.releases uses `git ls-remote`); openssh-client is REQUIRED by the atlas builder,
# which reaches a build VM's guest by shelling out to `ssh` through a ProxyJump host
# (chef.builders.atlas); uv does the install.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app

# Copy metadata first so the dependency layer caches across source edits.
COPY pyproject.toml README.md ./
COPY chef ./chef
COPY recipes ./recipes

RUN uv pip install --system --no-cache .

EXPOSE 8000

# Overridden by docker-compose for the worker service.
CMD ["chef", "serve", "--host", "0.0.0.0", "--port", "8000"]
