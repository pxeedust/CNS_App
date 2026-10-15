#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
host="${1%.}"
bind_ip="${2:-}"
if [[ ! "$host" =~ ^[a-z0-9-]+([.][a-z0-9-]+)+$ ]]; then
  echo 'Pass the Tailscale Funnel hostname, without https://.' >&2
  exit 1
fi
test -f .env.production || { echo '.env.production is missing.' >&2; exit 1; }
if [[ -n "$bind_ip" ]] && {
  [[ ! "$bind_ip" =~ ^[0-9]+([.][0-9]+){3}$ ]] ||
  ! ip -4 -o addr show | grep -Fq " $bind_ip/"
}; then
  echo 'Second argument must be an IPv4 address on this VM.' >&2
  exit 1
fi

update_list() {
  local name="$1" item="$2" current
  current="$(sed -n "s/^${name}=//p" .env.production)"
  test -n "$current" || { echo "${name} is missing." >&2; exit 1; }
  case ",${current}," in
    *",${item},"*) return ;;
  esac
  sed -i "s#^${name}=.*#${name}=${current},${item}#" .env.production
}

umask 077
cp -p .env.production "backups/env-pre-funnel-$(date -u +%Y%m%dT%H%M%SZ)"
update_list ALLOWED_HOSTS "$host"
update_list CSRF_TRUSTED_ORIGINS "https://$host"
if [[ -n "$bind_ip" ]]; then
  if grep -q '^PROXY_BIND_IP=' .env.production; then
    sed -i "s#^PROXY_BIND_IP=.*#PROXY_BIND_IP=$bind_ip#" .env.production
  else
    printf '\nPROXY_BIND_IP=%s\n' "$bind_ip" >> .env.production
  fi
fi
echo "Added $host to Django host and CSRF settings."
