# Build the static SPA with Bun, serve it with nginx — which also reverse-proxies
# /api to the FastAPI service, so the browser is always same-origin (no CORS).
FROM oven/bun:1-alpine AS build
WORKDIR /app
# Deps first (cache layer): lockfile-exact, no network drift.
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY . .
RUN bun run build

FROM nginx:1.27-alpine AS serve
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=4s --start-period=5s --retries=6 \
  CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
