#!/usr/bin/env bash
set -euo pipefail

Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/ramon-xvfb.log 2>&1 &
xvfb_pid=$!
trap 'wineserver -k 2>/dev/null || true; kill "$xvfb_pid" 2>/dev/null || true' EXIT

for _ in {1..50}; do
  [[ -S /tmp/.X11-unix/X99 ]] && break
  kill -0 "$xvfb_pid"
  sleep 0.1
done
[[ -S /tmp/.X11-unix/X99 ]] || { echo 'Xvfb did not start' >&2; exit 1; }

x11vnc -display :99 -localhost -nopw -forever -shared -rfbport 5900 \
  >/tmp/ramon-vnc.log 2>&1 &
vnc_pid=$!
websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 \
  >/tmp/ramon-websockify.log 2>&1 &
web_pid=$!
trap 'wineserver -k 2>/dev/null || true; kill "$web_pid" "$vnc_pid" "$xvfb_pid" 2>/dev/null || true' EXIT

wineboot --init
echo 'MT5 desktop: http://127.0.0.1:6080/vnc.html (published only on host loopback)'
echo 'Install a user-supplied MT5 installer, then compile Ramon and launch terminal.'
wait "$web_pid"
