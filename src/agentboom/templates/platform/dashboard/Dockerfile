FROM node:22-alpine

WORKDIR /app

# Workspace manifests first for layer caching.
COPY package.json ./
COPY ui/package.json ui/
COPY dashboard/package.json dashboard/

# Install all workspace dependencies.
RUN npm install --no-audit --no-fund

# The source is bind-mounted by docker-compose (./ui and ./dashboard), so a
# restart serves the current source: the entrypoint builds ui + dashboard at
# container start, then starts the production server.

EXPOSE 3000

ENTRYPOINT ["/bin/sh", "/app/dashboard/entrypoint.sh"]
