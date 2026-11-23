#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  AETHER: start everything with one command.
#
#    ./start.sh              backend + web app, opens your browser
#    ./start.sh --no-open    don't open the browser
#
#  Ctrl+C stops both. The first run also installs what's missing.
#  Ports can be changed:  API_PORT=8001 UI_PORT=3001 ./start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="${ROOT}/backend"
FRONTEND="${ROOT}/frontend"
API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-3000}"
OPEN_BROWSER=1
[[ "${1:-}" == "--no-open" ]] && OPEN_BROWSER=0

# ── pretty output ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_CYAN=$'\033[38;2;125;211;252m'; C_SEA=$'\033[38;2;94;234;212m'; C_PERI=$'\033[38;2;165;180;252m'
  C_AMBER=$'\033[38;2;252;211;77m'; C_CORAL=$'\033[38;2;253;164;175m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_CYAN=""; C_SEA=""; C_PERI=""; C_AMBER=""; C_CORAL=""; C_DIM=""; C_OFF=""
fi
say()  { printf "%s◉%s %s\n" "${C_CYAN}" "${C_OFF}" "$*"; }
ok()   { printf "%s✓%s %s\n" "${C_SEA}" "${C_OFF}" "$*"; }
warn() { printf "%s!%s %s\n" "${C_AMBER}" "${C_OFF}" "$*"; }
die()  { printf "%s✗ %s%s\n" "${C_CORAL}" "$*" "${C_OFF}"; exit 1; }

# Line-buffered prefixing of each server's output ([api] / [ui]).
if sed --version >/dev/null 2>&1; then SED_UNBUF="-u"; else SED_UNBUF="-l"; fi
prefix() { sed ${SED_UNBUF} "s/^/$1 /"; }

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
wait_for() {  # url, seconds, pid: succeeds when the url answers; fails if pid dies first
  local i
  for ((i = 0; i < $2 * 4; i++)); do
    curl -sf -o /dev/null "$1" && return 0
    kill -0 "$3" 2>/dev/null || return 1
    sleep 0.25
  done
  return 1
}

printf "\n%s  A E T H E R%s %s· local autonomous agent%s\n\n" "${C_CYAN}" "${C_OFF}" "${C_DIM}" "${C_OFF}"

# ── prerequisites ────────────────────────────────────────────────────────────
command -v node >/dev/null 2>&1 || die "Node.js isn't installed. Get it from https://nodejs.org"
command -v curl >/dev/null 2>&1 || die "curl is required."

if [[ ! -x "${BACKEND}/.venv/bin/python" ]]; then
  say "First run: creating the Python environment (one minute)…"
  PY="$(command -v python3 || true)"
  [[ -n "${PY}" ]] || die "Python 3.11+ isn't installed."
  "${PY}" -m venv "${BACKEND}/.venv" || die "Couldn't create backend/.venv"
  "${BACKEND}/.venv/bin/pip" install -q --upgrade pip
  "${BACKEND}/.venv/bin/pip" install -q -r "${BACKEND}/requirements.txt" || die "pip install failed."
  ok "Python environment ready"
fi

if [[ ! -d "${FRONTEND}/node_modules" ]]; then
  say "First run: installing the web app's packages…"
  (cd "${FRONTEND}" && npm install --no-audit --no-fund) || die "npm install failed."
  ok "Web app packages ready"
fi

if curl -sf -o /dev/null http://localhost:11434/api/version; then
  ok "Ollama is running"
else
  warn "Ollama isn't running. Open the Ollama app (AETHER will tell you in the UI until it's up)."
  [[ "$(uname)" == "Darwin" ]] && open -ga Ollama 2>/dev/null && say "Tried to start Ollama for you…"
fi

# Next.js allows only one dev server per folder, whatever the port.
if [[ -f "${FRONTEND}/.next/dev/lock" ]] && pgrep -f "${FRONTEND}.*next dev|next dev.*" >/dev/null 2>&1 \
   && lsof -nP -c node -a -d cwd 2>/dev/null | grep -q "${FRONTEND}"; then
  die "The AETHER web app is already running in another terminal (npm run dev).
  Stop it there with Ctrl+C, then run ./start.sh again."
fi

for p in "${API_PORT}" "${UI_PORT}"; do
  if port_busy "${p}"; then
    holder="$(lsof -nP -iTCP:"${p}" -sTCP:LISTEN | awk 'NR==2 {print $1" (PID "$2")"}')"
    die "Port ${p} is already in use by ${holder}. If it's an older AETHER, stop it with:
    kill \$(lsof -tiTCP:${p} -sTCP:LISTEN)
  …then run ./start.sh again."
  fi
done

# ── start both servers ───────────────────────────────────────────────────────
API_PID=""; UI_PID=""
cleanup() {
  local code=$?
  trap - INT TERM EXIT
  if [[ -n "${API_PID}${UI_PID}" ]]; then
    printf "\n"
    say "Stopping AETHER…"
    [[ -n "${UI_PID}" ]] && kill "${UI_PID}" 2>/dev/null
    [[ -n "${API_PID}" ]] && kill "${API_PID}" 2>/dev/null
    wait 2>/dev/null
    ok "Stopped. See you soon."
  fi
  exit "${code}"
}
trap cleanup INT TERM EXIT

say "Starting the backend on port ${API_PORT}…"
(
  cd "${BACKEND}" || exit 1
  export PYTHONUNBUFFERED=1 AETHER_API_PORT="${API_PORT}"
  export AETHER_CORS_ORIGINS="[\"http://localhost:${UI_PORT}\",\"http://127.0.0.1:${UI_PORT}\"]"
  exec .venv/bin/python api.py
) > >(prefix "${C_PERI}[api]${C_OFF}") 2>&1 &
API_PID=$!

wait_for "http://127.0.0.1:${API_PORT}/api/health" 40 "${API_PID}" || {
  warn "The backend didn't start. See the [api] lines above."; exit 1; }
ok "Backend ready"

say "Starting the web app on port ${UI_PORT}…"
(
  cd "${FRONTEND}" || exit 1
  export NEXT_PUBLIC_AETHER_API="http://127.0.0.1:${API_PORT}" NEXT_TELEMETRY_DISABLED=1
  exec ./node_modules/.bin/next dev --port "${UI_PORT}"
) > >(prefix "${C_CYAN}[ui] ${C_OFF}") 2>&1 &
UI_PID=$!

if wait_for "http://localhost:${UI_PORT}" 90 "${UI_PID}"; then
  printf "\n%s  AETHER is running →%s http://localhost:%s   %s(Ctrl+C to stop)%s\n\n" \
    "${C_SEA}" "${C_OFF}" "${UI_PORT}" "${C_DIM}" "${C_OFF}"
  if [[ ${OPEN_BROWSER} -eq 1 ]]; then
    if command -v open >/dev/null 2>&1; then open "http://localhost:${UI_PORT}"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:${UI_PORT}" >/dev/null 2>&1; fi
  fi
elif ! kill -0 "${UI_PID}" 2>/dev/null; then
  warn "The web app stopped while starting. See the [ui] lines above."
  exit 1
else
  warn "The web app is taking a while. Watch the [ui] lines above."
fi

# If either server stops, stop the other too.
while kill -0 "${API_PID}" 2>/dev/null && kill -0 "${UI_PID}" 2>/dev/null; do
  sleep 1
done
warn "One of the servers stopped."
cleanup
