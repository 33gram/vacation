FROM node:22-slim

# T-M1-24: build context is the REPO ROOT (infra/docker-compose.yml,
# `context: ..`, `dockerfile: frontend/Dockerfile`), not frontend/ alone
# - `scripts/check-node-version.mjs`'s preinstall hook reads `.nvmrc`
# two directories above itself (frontend/scripts/../.. = repo root in
# the real checkout), and that relative path only resolves if the image
# preserves the same depth: `.nvmrc` at /app, frontend's own files one
# level below at /app/frontend. Discovered by running, not assumed: no
# CI job had ever actually built this image before adding the E2E job
# (frontend-tests/openapi-client-check both run npm directly on the
# runner's checkout, never via Docker) - a fresh, uncached build
# immediately failed `npm ci`'s preinstall step with "Не удалось
# прочитать .nvmrc" because the old `context: ../frontend` never
# contained that file at all. Local dev never saw this because the
# `npm ci` layer, once built, is cached by package.json/package-lock.json
# content and never invalidated since - see commit body for the exact
# failing log.
WORKDIR /app

COPY .nvmrc ./.nvmrc

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
# `scripts/` copied here, not with the rest of frontend/ below - npm's
# own `preinstall` hook (package.json) runs `node scripts/check-node-
# version.mjs` as part of `npm ci` itself, before anything else is
# copied; without this, `npm ci` fails with MODULE_NOT_FOUND on a fresh
# build regardless of the `.nvmrc` path question above (found by
# running, see commit body).
COPY frontend/scripts ./scripts
RUN npm ci

COPY frontend/. .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
