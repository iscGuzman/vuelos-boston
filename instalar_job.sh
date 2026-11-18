#!/bin/zsh
# Instala un job de macOS (launchd) que corre `vuelos.py run` cada 3 horas,
# incluida la madrugada. Desinstalar: ./instalar_job.sh quitar
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.vuelos.monitor-bos"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "$1" == "quitar" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "Job eliminado."; exit 0
fi

[[ -x "$DIR/.venv/bin/python" ]] || { python3 -m venv "$DIR/.venv"; "$DIR/.venv/bin/pip" install -q fast-flights typing_extensions; }

INTERVALOS=""
for h in 0 3 6 9 12 15 18 21; do
  INTERVALOS+="<dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>7</integer></dict>"
done

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>$DIR/vuelos.py</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><array>$INTERVALOS</array>
  <key>StandardOutPath</key><string>$DIR/monitor.log</string>
  <key>StandardErrorPath</key><string>$DIR/monitor.err.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Job instalado: corre cada 3 h. Ver resultados con: $DIR/.venv/bin/python $DIR/vuelos.py report"
