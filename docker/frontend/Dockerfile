# Baseline11 web UI (Next.js). NEXT_PUBLIC_* values are compiled into the
# client bundle, so they are build arguments.
# Build from the repo root:
#   docker build -f docker/frontend/Dockerfile -t baseline11/frontend \
#     --build-arg NEXT_PUBLIC_OIDC_AUTHORITY=https://idp.example.com \
#     --build-arg NEXT_PUBLIC_OIDC_CLIENT_ID=... .
FROM node:22-alpine AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
ARG NEXT_PUBLIC_OIDC_AUTHORITY=""
ARG NEXT_PUBLIC_OIDC_CLIENT_ID=""
ARG NEXT_PUBLIC_OIDC_SCOPE="openid profile email"
ARG NEXT_PUBLIC_OIDC_AUDIENCE=""
RUN npm run build && npm prune --omit=dev

FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /web/package.json /web/next.config.mjs ./
COPY --from=build --chown=node:node /web/node_modules ./node_modules
COPY --from=build --chown=node:node /web/.next ./.next
USER node
EXPOSE 3000
# In AWS the load balancer sends /api/* to the API service directly;
# BACKEND_URL only matters when this container proxies /api itself.
CMD ["node_modules/.bin/next", "start", "-p", "3000"]
