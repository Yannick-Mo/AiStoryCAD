# ── Build stage: compile the Vite/React app ─────────────────────────────
FROM node:20.18-alpine AS build

WORKDIR /app

RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 appuser

# Install locked deps (package-lock.json is committed). --legacy-peer-deps is
# kept to avoid ERESOLVE failures from an unresolved real peer conflict;
# TODO: drop the flag once the underlying peer deps are aligned.
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps --no-audit --no-fund && npm cache clean --force

COPY . .

RUN npm run build

# ── Runtime stage: static hosting + API/MCP reverse proxy via nginx ──────
FROM nginxinc/nginx-unprivileged:alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

# ── Dev stage: full node toolchain for `vite dev` (docker-compose.dev.yml) ─
FROM node:20.18-alpine AS dev

WORKDIR /app

RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 appuser

COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps --no-audit --no-fund && npm cache clean --force

COPY . .

RUN chown -R appuser:appuser /app

EXPOSE 5173

USER appuser

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]