#!/usr/bin/env bash
# Builds AETHER.app in ~/Applications: double-click to start AETHER, Quit (⌘Q) to stop it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${HOME}/Applications/AETHER.app"
LOG="${HOME}/Library/Logs/AETHER.log"
SRC="$(mktemp -d)/aether.applescript"
cat > "${SRC}" <<APPLESCRIPT
property rootDir : "${ROOT}"
property logFile : "${LOG}"

on run
	set alreadyUp to (do shell script "curl -sf -o /dev/null http://localhost:3000 && echo yes || echo no")
	if alreadyUp is "no" then
		do shell script "cd " & quoted form of rootDir & " && /bin/zsh -lc './start.sh --no-open' > " & quoted form of logFile & " 2>&1 & echo \$! > /tmp/aether-app.pid"
		repeat 180 times
			if (do shell script "curl -sf -o /dev/null http://localhost:3000 && echo yes || echo no") is "yes" then exit repeat
			delay 0.5
		end repeat
	end if
	do shell script "if [ -d '/Applications/Google Chrome.app' ]; then open -na 'Google Chrome' --args --app=http://localhost:3000; else open http://localhost:3000; fi"
end run

on quit
	do shell script "if [ -f /tmp/aether-app.pid ]; then pkill -TERM -P \$(cat /tmp/aether-app.pid) 2>/dev/null; kill -TERM \$(cat /tmp/aether-app.pid) 2>/dev/null; rm -f /tmp/aether-app.pid; fi; true"
	continue quit
end quit
APPLESCRIPT
mkdir -p "${HOME}/Applications"
rm -rf "${OUT}"
osacompile -s -o "${OUT}" "${SRC}"
echo "Built ${OUT}  (logs: ${LOG})"
