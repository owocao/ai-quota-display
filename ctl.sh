#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$DIR/logs"
mkdir -p "$LOG_DIR"

# 自动加载同目录 config.env（命令行传入的环境变量优先）
if [[ -f "$DIR/config.env" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "${k// }" || "${k#\#}" != "$k" ]] && continue
    k="${k// /}"
    v="${v%$'\r'}"
    if [[ -z "${!k+x}" ]]; then
      export "$k=$v"
    fi
  done < "$DIR/config.env"
fi

QUOTA_PORT="${QUOTA_PORT:-8787}"
POLL_INTERVAL="${POLL_INTERVAL:-120}"
ADB_WIRELESS_TARGET="${ADB_WIRELESS_TARGET:-}"
PYTHON_BIN="$(command -v python3 || echo /opt/homebrew/bin/python3)"
ADB_BIN="$(command -v adb || echo /opt/homebrew/bin/adb)"
CODEXBAR_BIN="$(command -v codexbar || echo /opt/homebrew/bin/codexbar)"

AGENT_DIR="$HOME/Library/LaunchAgents"
SERVER_LABEL="com.local.ai-quota-display.server"
SYNC_LABEL="com.local.ai-quota-display.screen-sync"
SERVER_PLIST="$AGENT_DIR/${SERVER_LABEL}.plist"
SYNC_PLIST="$AGENT_DIR/${SYNC_LABEL}.plist"
UID_NUM="$(id -u)"

is_autostart_enabled() {
  [[ -f "$SERVER_PLIST" && -f "$SYNC_PLIST" ]]
}

is_launchd_loaded() {
  local label="$1"
  launchctl print "gui/${UID_NUM}/${label}" >/dev/null 2>&1
}

pid_running() {
  local name="$1"
  local pid_file="$LOG_DIR/${name}.pid"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "$pid"
      return 0
    fi
  fi
  return 1
}

write_plists() {
  mkdir -p "$AGENT_DIR"

  cat > "$SERVER_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${SERVER_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${DIR}/server.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>QUOTA_PORT</key>
    <string>${QUOTA_PORT}</string>
    <key>POLL_INTERVAL</key>
    <string>${POLL_INTERVAL}</string>
    <key>CODEXBAR_BIN</key>
    <string>${CODEXBAR_BIN}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/server.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/server.log</string>
</dict>
</plist>
EOF

  cat > "$SYNC_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${SYNC_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${DIR}/screen_sync.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>QUOTA_PORT</key>
    <string>${QUOTA_PORT}</string>
    <key>ADB_BIN</key>
    <string>${ADB_BIN}</string>
    <key>ADB_WIRELESS_TARGET</key>
    <string>${ADB_WIRELESS_TARGET}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/screen_sync.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/screen_sync.log</string>
</dict>
</plist>
EOF
}

stop_manual_only() {
  for name in server screen_sync; do
    local pid_file="$LOG_DIR/${name}.pid"
    if [[ -f "$pid_file" ]]; then
      local pid
      pid="$(cat "$pid_file")"
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
      fi
      rm -f "$pid_file"
    fi
  done

  # 防呆兜底：清理因目录移动未先停止而残留的旧进程
  local port_pids
  port_pids="$(lsof -tiTCP:"${QUOTA_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$port_pids" ]]; then
    for p in $port_pids; do
      if ps -p "$p" -o command= 2>/dev/null | grep -q "server\.py"; then
        kill "$p" 2>/dev/null || true
      fi
    done
  fi

  local sync_pids
  sync_pids="$(pgrep -f "screen_sync\.py" 2>/dev/null || true)"
  if [[ -n "$sync_pids" ]]; then
    for p in $sync_pids; do
      kill "$p" 2>/dev/null || true
    done
  fi
}

plist_matches_current_dir() {
  [[ -f "$SERVER_PLIST" ]] && grep -Fq "<string>${DIR}/server.py</string>" "$SERVER_PLIST"
}

cmd_start() {
  if is_autostart_enabled; then
    stop_manual_only
    if ! plist_matches_current_dir; then
      echo "检测到项目目录已移动，自动更新开机自启动路径..."
      cmd_enable
      return
    fi
    for pair in "${SERVER_LABEL}:${SERVER_PLIST}" "${SYNC_LABEL}:${SYNC_PLIST}"; do
      local label="${pair%%:*}"
      local plist="${pair#*:}"
      if is_launchd_loaded "$label"; then
        launchctl kickstart -k "gui/${UID_NUM}/${label}" >/dev/null 2>&1 || true
      else
        launchctl bootstrap "gui/${UID_NUM}" "$plist" >/dev/null 2>&1 || true
      fi
    done
    echo "已通过 LaunchAgent 启动服务（端口 ${QUOTA_PORT}）。"
  else
    stop_manual_only
    sleep 0.3
    QUOTA_PORT="$QUOTA_PORT" POLL_INTERVAL="$POLL_INTERVAL" CODEXBAR_BIN="$CODEXBAR_BIN" \
      nohup "$PYTHON_BIN" "$DIR/server.py" > "$LOG_DIR/server.log" 2>&1 &
    echo $! > "$LOG_DIR/server.pid"

    QUOTA_PORT="$QUOTA_PORT" ADB_BIN="$ADB_BIN" ADB_WIRELESS_TARGET="$ADB_WIRELESS_TARGET" \
      nohup "$PYTHON_BIN" "$DIR/screen_sync.py" > "$LOG_DIR/screen_sync.log" 2>&1 &
    echo $! > "$LOG_DIR/screen_sync.pid"

    echo "已手动后台启动服务（端口 ${QUOTA_PORT}）。"
  fi
}

cmd_stop() {
  if is_launchd_loaded "$SERVER_LABEL"; then
    launchctl bootout "gui/${UID_NUM}/${SERVER_LABEL}" >/dev/null 2>&1 || true
  fi
  if is_launchd_loaded "$SYNC_LABEL"; then
    launchctl bootout "gui/${UID_NUM}/${SYNC_LABEL}" >/dev/null 2>&1 || true
  fi
  stop_manual_only
  echo "已停止仪表盘服务与锁屏联动。"
}

cmd_restart() {
  cmd_stop
  sleep 0.6
  cmd_start
}

cmd_enable() {
  cmd_stop >/dev/null 2>&1 || true
  write_plists
  launchctl bootstrap "gui/${UID_NUM}" "$SERVER_PLIST"
  launchctl bootstrap "gui/${UID_NUM}" "$SYNC_PLIST"
  echo "已开启开机自启动（已绑定当前目录），并立即拉起服务。"
}

cmd_disable() {
  if is_launchd_loaded "$SERVER_LABEL"; then
    launchctl bootout "gui/${UID_NUM}/${SERVER_LABEL}" >/dev/null 2>&1 || true
  fi
  if is_launchd_loaded "$SYNC_LABEL"; then
    launchctl bootout "gui/${UID_NUM}/${SYNC_LABEL}" >/dev/null 2>&1 || true
  fi
  rm -f "$SERVER_PLIST" "$SYNC_PLIST"
  echo "已关闭开机自启动并移除 LaunchAgent 配置。"
}

cmd_status() {
  echo "================ AI Quota Display 状态 ================"
  echo "项目目录   : ${DIR}"
  if is_autostart_enabled; then
    echo "开机自启动 : 已开启 (LaunchAgent)"
  else
    echo "开机自启动 : 未开启"
  fi

  local srv_state="未运行"
  if is_launchd_loaded "$SERVER_LABEL"; then
    local spid
    spid="$(launchctl print "gui/${UID_NUM}/${SERVER_LABEL}" 2>/dev/null | awk '/pid =/{print $3; exit}')"
    srv_state="运行中 (LaunchAgent, PID ${spid:-?})"
  elif spid="$(pid_running server)"; then
    srv_state="运行中 (手动, PID ${spid})"
  fi
  echo "仪表盘服务 : ${srv_state} | http://127.0.0.1:${QUOTA_PORT}"

  local sync_state="未运行"
  if is_launchd_loaded "$SYNC_LABEL"; then
    local ypid
    ypid="$(launchctl print "gui/${UID_NUM}/${SYNC_LABEL}" 2>/dev/null | awk '/pid =/{print $3; exit}')"
    sync_state="运行中 (LaunchAgent, PID ${ypid:-?})"
  elif ypid="$(pid_running screen_sync)"; then
    sync_state="运行中 (手动, PID ${ypid})"
  fi
  echo "锁屏联动   : ${sync_state}"

  local dev_line
  dev_line="$("$ADB_BIN" devices 2>/dev/null | awk 'NR>1 && $2=="device"{print $1}' | tr '\n' ' ' || true)"
  echo "ADB 设备   : ${dev_line:-无在线设备 (目标 ${ADB_WIRELESS_TARGET:-USB})}"
  echo "======================================================="
}

cmd_logs() {
  echo "--- server.log (最近 10 行) ---"
  tail -n 10 "$LOG_DIR/server.log" 2>/dev/null || echo "(无)"
  echo "--- screen_sync.log (最近 10 行) ---"
  tail -n 10 "$LOG_DIR/screen_sync.log" 2>/dev/null || echo "(无)"
}

interactive_menu() {
  while true; do
    echo ""
    cmd_status
    echo "请选择操作："
    echo "  1) 启动"
    echo "  2) 停止"
    echo "  3) 重启"
    echo "  4) 查看状态"
    echo "  5) 开启开机自启动"
    echo "  6) 关闭开机自启动"
    echo "  7) 查看最近日志"
    echo "  0) 退出"
    printf "输入编号 [0-7]: "
    read -r choice
    echo ""
    case "$choice" in
      1) cmd_start ;;
      2) cmd_stop ;;
      3) cmd_restart ;;
      4) ;;
      5) cmd_enable ;;
      6) cmd_disable ;;
      7) cmd_logs ;;
      0|q|Q) exit 0 ;;
      *) echo "无效选项: $choice" ;;
    esac
  done
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  enable) cmd_enable ;;
  disable) cmd_disable ;;
  logs) cmd_logs ;;
  "") interactive_menu ;;
  *)
    echo "用法: $0 [start|stop|restart|status|enable|disable|logs]"
    exit 1
    ;;
esac
