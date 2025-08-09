#!/usr/bin/with-contenv bashio

# Read configuration from options.json
EXPORT_DIR=$(bashio::config 'export_dir')
DB_HOST=$(bashio::config 'db_host')
DB_NAME=$(bashio::config 'db_name')
DB_USER=$(bashio::config 'db_user')
DB_PASS=$(bashio::config 'db_pass')
IMMICH_USER_ID=$(bashio::config 'immich_user_id')
SCHEDULE=$(bashio::config 'schedule')
HA_PUSH_INTERVAL_SEC=$(bashio::config 'ha_push_interval_sec')


# Single library mount path
ASSETS_ROOT=$(bashio::config 'assets_root')

# Deletion guard thresholds (may be unset -> "null")
MIN_FOUND_ABS=$(bashio::config 'min_found_abs')
MIN_FOUND_FRACTION=$(bashio::config 'min_found_fraction')

# Default advanced options if empty or "null"
if [ -z "$MIN_FOUND_ABS" ] || [ "$MIN_FOUND_ABS" = "null" ]; then
  MIN_FOUND_ABS=100
fi
if [ -z "$MIN_FOUND_FRACTION" ] || [ "$MIN_FOUND_FRACTION" = "null" ]; then
  MIN_FOUND_FRACTION=0.05
fi

# Export environment variables for all child processes (including webgui & cron)
export EXPORT_DIR DB_HOST DB_NAME DB_USER DB_PASS IMMICH_USER_ID
export ASSETS_ROOT MIN_FOUND_ABS MIN_FOUND_FRACTION
export HA_PUSH_INTERVAL_SEC

# Log configuration (redact password)
bashio::log.info "Starting Immich Album Export"
bashio::log.info "Export Directory: ${EXPORT_DIR}"
bashio::log.info "Database Host: ${DB_HOST}"
bashio::log.info "Database Name: ${DB_NAME}"
bashio::log.info "Database User: ${DB_USER}"
bashio::log.info "Immich User ID: ${IMMICH_USER_ID}"
bashio::log.info "Schedule: ${SCHEDULE}"
bashio::log.info "Assets Root (library mount): ${ASSETS_ROOT}"
bashio::log.info "Safety thresholds: MIN_FOUND_ABS=${MIN_FOUND_ABS}, MIN_FOUND_FRACTION=${MIN_FOUND_FRACTION}"

# Create export directory if it doesn't exist
mkdir -p "${EXPORT_DIR}"

# Create cron job with the schedule from options.json
bashio::log.info "Creating cron job with schedule: ${SCHEDULE}"
cat > /etc/cron.d/immich_export << EOF
${SCHEDULE} root EXPORT_DIR="${EXPORT_DIR}" DB_HOST="${DB_HOST}" DB_NAME="${DB_NAME}" DB_USER="${DB_USER}" DB_PASS="${DB_PASS}" IMMICH_USER_ID="${IMMICH_USER_ID}" ASSETS_ROOT="${ASSETS_ROOT}" MIN_FOUND_ABS="${MIN_FOUND_ABS}" MIN_FOUND_FRACTION="${MIN_FOUND_FRACTION}" /usr/bin/python3 /usr/src/app/export_immich_albums_db.py >> /tmp/immich_export.log 2>&1
EOF
chmod 0644 /etc/cron.d/immich_export

# Start Flask GUI in background (after exports are set)
bashio::log.info "Starting web GUI on port 5000"
python3 /usr/src/app/webgui.py &

# Run the export once immediately (optional)
bashio::log.info "Running initial export..."
python3 /usr/src/app/export_immich_albums_db.py || bashio::log.error "Initial export failed"

# Start cron daemon in foreground
bashio::log.info "Starting cron daemon in foreground..."
exec crond -f -l 4

