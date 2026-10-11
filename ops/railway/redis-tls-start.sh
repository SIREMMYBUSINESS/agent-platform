#!/bin/sh
# Installed as the Redis service's start command by enable-redis-tls.ps1.
set -eu
umask 077
: "${REDIS_TLS_CERT_B64:?Missing server certificate}"
: "${REDIS_TLS_KEY_B64:?Missing server key}"
: "${REDIS_TLS_CA_CERT_B64:?Missing CA certificate}"
: "${REDIS_PASSWORD:?Missing Redis password}"
: "${RAILWAY_VOLUME_MOUNT_PATH:?Missing Redis data volume}"

tls_dir=$(mktemp -d /tmp/redis-tls.XXXXXXXX)
printf '%s' "$REDIS_TLS_CERT_B64" | base64 -d > "$tls_dir/server.pem"
printf '%s' "$REDIS_TLS_KEY_B64" | base64 -d > "$tls_dir/server.key"
printf '%s' "$REDIS_TLS_CA_CERT_B64" | base64 -d > "$tls_dir/ca.pem"
# Quote the password for redis.conf without passing it in process arguments.
password=$(printf '%s' "$REDIS_PASSWORD" | sed 's/\\/\\\\/g; s/"/\\"/g')
if [ "$(printf '%s' "$REDIS_PASSWORD" | tr -d '\r\n')" != "$REDIS_PASSWORD" ]; then
    echo 'Redis password must not contain line breaks' >&2; exit 1
fi
cat > "$tls_dir/redis.conf" <<EOF
bind 0.0.0.0 ::
protected-mode yes
port 0
tls-port 6379
tls-cert-file $tls_dir/server.pem
tls-key-file $tls_dir/server.key
tls-ca-cert-file $tls_dir/ca.pem
tls-auth-clients no
tls-protocols "TLSv1.2 TLSv1.3"
requirepass "$password"
save 60 1
EOF
chmod 400 "$tls_dir/server.pem" "$tls_dir/server.key" "$tls_dir/ca.pem" "$tls_dir/redis.conf"
chown redis:redis "$tls_dir" "$tls_dir/server.pem" "$tls_dir/server.key" "$tls_dir/ca.pem" "$tls_dir/redis.conf"
unset REDIS_TLS_KEY_B64 REDIS_PASSWORD password
exec docker-entrypoint.sh redis-server "$tls_dir/redis.conf" --dir "$RAILWAY_VOLUME_MOUNT_PATH"
