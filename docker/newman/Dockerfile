# Pinned, non-root Newman runtime for Baseline11 execution jobs
# (spec §5.1 item 5 "pinned Newman 6.2.2 runtime", §8 "non-root container").
# Build: docker build -t baseline11/newman:6.2.2 docker/newman
FROM node:22-alpine
RUN npm install --global --no-audit --no-fund newman@6.2.2 \
    && npm cache clean --force
USER node
WORKDIR /home/node
ENTRYPOINT ["newman"]
