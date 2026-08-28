#!/bin/sh
set -e
cd /app

# Build the current source at container start: ui (tsc) first, because the
# dashboard imports @agentboom/ui from its dist/.
npm run build -w @agentboom/ui
npm run build -w @agentboom/dashboard

# Production server. 127.0.0.1, not localhost: next-server binds IPv4 only
# and localhost would resolve to ::1 in this container.
exec npm run start -w @agentboom/dashboard -- -H 127.0.0.1
