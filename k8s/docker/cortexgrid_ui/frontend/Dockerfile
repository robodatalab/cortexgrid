# CortexGrid UI — frontend (Vite build served by nginx, proxies /api to backend)

FROM node:22-alpine AS builder

WORKDIR /app

COPY cortexgrid_ui/frontend/package.json cortexgrid_ui/frontend/package-lock.json ./
RUN npm ci

COPY cortexgrid_ui/frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY k8s/docker/cortexgrid_ui/frontend/nginx.conf /etc/nginx/conf.d/default.conf
