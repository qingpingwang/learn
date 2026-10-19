#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
src="$root/ComfyUI"
venv="$root/.venv"
piddir="$root/pid"
logdir="$root/log"
pidfile="$piddir/comfyui.pid"
port=${PORT:-8188}
PATH="$HOME/.local/bin:$PATH"

if [ ! -f "$src/main.py" ]; then
  echo "comfyui source missing, run: git submodule update --init" >&2
  exit 1
fi

mkdir -p "$piddir" "$logdir"

if [ -f "$pidfile" ]; then
  old=$(tr -d '[:space:]' < "$pidfile")
  if kill -0 "$old" 2>/dev/null; then
    echo "comfyui already running: $old" >&2
    exit 1
  fi
  rm -f "$pidfile"
fi

if [ ! -x "$venv/bin/python" ]; then
  echo "creating venv ..."
  command -v uv >/dev/null || {
    echo "uv not found, install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
  }
  uv venv "$venv" --python 3.13
  uv pip install --python "$venv/bin/python" -r "$src/requirements.txt"
fi

cd "$src"
nohup "$venv/bin/python" -u main.py --listen 127.0.0.1 --port "$port" >> "$logdir/comfyui.log" 2>&1 &
echo $! > "$pidfile"

echo "waiting for comfyui ..."
i=0
until curl -fs -o /dev/null "http://127.0.0.1:$port/system_stats"; do
  if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "failed to start comfyui, see log/comfyui.log" >&2
    rm -f "$pidfile"
    exit 1
  fi
  i=$((i + 1))
  if [ "$i" -ge 180 ]; then
    echo "comfyui not ready, see log/comfyui.log" >&2
    exit 1
  fi
  sleep 1
done

echo "comfyui started: http://127.0.0.1:$port ($(cat "$pidfile"))"
