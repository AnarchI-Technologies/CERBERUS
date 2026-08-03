#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this helper as root." >&2
    exit 1
fi

target="/etc/wsl.conf"
if [ -f "$target" ] &&
   awk '
       BEGIN { in_boot = 0; enabled = 0 }
       /^\[boot\][[:space:]]*$/ { in_boot = 1; next }
       /^\[/ { in_boot = 0 }
       in_boot && /^[[:space:]]*systemd[[:space:]]*=[[:space:]]*true[[:space:]]*$/ { enabled = 1 }
       END { exit enabled ? 0 : 1 }
   ' "$target"; then
    echo "systemd is already enabled in /etc/wsl.conf"
    exit 0
fi

temporary="$(mktemp /etc/wsl.conf.cerberus.XXXXXX)"
cleanup() {
    rm -f "$temporary"
}
trap cleanup EXIT

if [ -f "$target" ]; then
    backup="/etc/wsl.conf.cerberus-backup.$(date -u '+%Y%m%d%H%M%S')"
    cp -a "$target" "$backup"
    awk '
        BEGIN { in_boot = 0; saw_boot = 0; wrote_systemd = 0 }
        /^\[boot\][[:space:]]*$/ {
            if (in_boot && !wrote_systemd) print "systemd=true"
            in_boot = 1
            saw_boot = 1
            wrote_systemd = 0
            print
            next
        }
        /^\[/ {
            if (in_boot && !wrote_systemd) print "systemd=true"
            in_boot = 0
        }
        in_boot && /^[[:space:]]*systemd[[:space:]]*=/ {
            if (!wrote_systemd) print "systemd=true"
            wrote_systemd = 1
            next
        }
        { print }
        END {
            if (in_boot && !wrote_systemd) print "systemd=true"
            if (!saw_boot) {
                print ""
                print "[boot]"
                print "systemd=true"
            }
        }
    ' "$target" >"$temporary"
else
    {
        echo "[boot]"
        echo "systemd=true"
    } >"$temporary"
fi

install -o root -g root -m 0644 "$temporary" "$target"
echo "systemd was enabled in /etc/wsl.conf"
echo "Restart WSL before continuing."
