FROM node:18-alpine AS builder

WORKDIR /app

# Install dependencies — use npm install instead of npm ci for compatibility
# with external submodules where package-lock.json may be out of sync.
COPY package*.json ./
RUN npm install --legacy-peer-deps || npm ci || true

# Copy source and build
COPY . .

# Build-time variables (now unused — frontend uses same-origin at runtime).
# Kept for backward compatibility with local dev builds.
ARG VITE_NAUTILUS_API_URL=
ARG VITE_WS_URL=
ARG VITE_ADMIN_DB_API_URL=

RUN npm run build

# ── Production image ─────────────────────────────────────────────────────────
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# Proxy config: forwards /api, /ws, and admin endpoints to backend services.
# This replaces the old static-only config that could not reach FastAPI.
# The VITE_* build args are now empty strings so the React code uses
# window.location.origin (same-origin) — nginx handles the reverse proxy.
COPY nginx-proxy.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]