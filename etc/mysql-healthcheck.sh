#!/bin/bash
set -eo pipefail

user="${MYSQL_USER:-root}"
export MYSQL_PWD="${MYSQL_PASSWORD:-$MYSQL_ROOT_PASSWORD}"

# Use the Unix socket (no -h) — avoids hostname resolution issues on Ubuntu 22
# and works regardless of MySQL's bind-address setting.
if ! mysqladmin -u"$user" ping --silent > /dev/null 2>&1; then
    echo "Healthcheck error: MySQL not responding"
    exit 1
fi

database_check=$(mysql -u"$user" --silent -e "SELECT COUNT(*) FROM oai_db.AuthenticationSubscription;" 2>/dev/null)
if [ -z "$database_check" ] || [ "$database_check" -eq 0 ]; then
    echo "Healthcheck error: oai_db.AuthenticationSubscription is empty or unreachable"
    exit 1
fi

exit 0
