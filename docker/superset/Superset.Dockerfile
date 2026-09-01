# The pinned upstream image (see docker-compose.yml for the exact tag +
# digest) doesn't bundle a Postgres driver by default -- its own
# docker-bootstrap.sh installs one at container start when
# DATABASE_DIALECT=postgres, but only for services that use the default
# entrypoint dispatch, which this compose file's superset-init/superset
# services don't (they run custom commands to reuse Superset's own
# docker-init.sh/docker-healthcheck.sh directly). Installing the driver
# once at build time, on top of the exact pinned base, is simpler and
# faster than re-running that install on every container start.
ARG SUPERSET_BASE_IMAGE=apache/superset:6.1.0@sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705
FROM ${SUPERSET_BASE_IMAGE}

USER root
RUN uv pip install --python /app/.venv/bin/python --no-cache psycopg2-binary
USER superset
