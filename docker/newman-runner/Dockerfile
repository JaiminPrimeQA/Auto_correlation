# Fargate runner task for Baseline11 execution jobs: one pinned Newman 6.2.2
# run per task (spec §5.2). No AWS credentials are ever given to this image;
# it talks to S3 only through presigned URLs passed in the environment.
# Build: docker build -t baseline11/newman-runner:6.2.2 docker/newman-runner
FROM node:22-alpine
RUN npm install --global --no-audit --no-fund newman@6.2.2 \
    && npm cache clean --force
WORKDIR /opt/runner
COPY run.mjs ./
USER node
ENV HOME=/tmp
ENTRYPOINT ["node", "/opt/runner/run.mjs"]
