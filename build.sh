#!/usr/bin/env bash
set -o errexit

# --- Node.js (for Tailwind CSS build) ---
NODE_VERSION="22.11.0"
NODE_DIR="node-v${NODE_VERSION}-linux-x64"

if [ ! -d "${NODE_DIR}" ]; then
  echo "==> Installing Node ${NODE_VERSION}..."
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_DIR}.tar.xz" -o node.tar.xz
  tar -xf node.tar.xz
  rm node.tar.xz
fi

export PATH="$PWD/${NODE_DIR}/bin:$PATH"
echo "==> Node: $(node --version), npm: $(npm --version)"

# --- Python dependencies ---
echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# --- Tailwind CSS build (npm ci + production build) ---
echo "==> Building Tailwind CSS..."
cd theme/static_src
npm ci --no-audit --no-fund
npm run build
cd ../..

# --- Django ---
echo "==> Collecting static files..."
python manage.py collectstatic --no-input

echo "==> Running migrations..."
python manage.py migrate --no-input

echo "==> Seeding tournament data..."
python manage.py seed_wc2026

# Backfill ganyan caches — idempotent safety net for results entered before
# the engine shipped, or any slot whose post_save signal got missed.
echo "==> Recomputing ganyan caches..."
python manage.py recompute_ganyan

echo "==> Build complete."
