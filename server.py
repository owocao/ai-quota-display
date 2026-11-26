#!/usr/bin/env python3
"""
AI Quota Display - 本地采集与 Web 仪表盘服务
仅监听 127.0.0.1，通过 adb reverse 映射给安卓手机显示。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
CACHE_FILE = BASE_DIR / "cache.json"

HOST = os.environ.get("QUOTA_HOST", "127.0.0.1")
PORT = int(os.environ.get("QUOTA_PORT", "8787"))
POLL_INTERVAL = max(30, int(os.environ.get("POLL_INTERVAL", "120")))
CODEXBAR_BIN = os.environ.get("CODEXBAR_BIN") or shutil.which("codexbar") or "/opt/homebrew/bin/codexbar"
PROVIDERS = ("codex", "antigravity")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_codexbar(provider: str) -> dict[str, Any]:
    cmd = [
        CODEXBAR_BIN,
        "usage",
        "--provider",
        provider,
        "--source",
        "oauth",
        "--format",
        "json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "provider": provider, "error": "查询超时（90s）"}
    except FileNotFoundError:
        return {"ok": False, "provider": provider, "error": f"未找到 {CODEXBAR_BIN}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": provider, "error": str(exc)}

    raw = (proc.stdout or "").strip()
    if not raw:
        err = (proc.stderr or "").strip() or f"退出码 {proc.returncode}"
        return {"ok": False, "provider": provider, "error": err}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "provider": provider, "error": f"JSON 解析失败: {exc}"}

    item = parsed[0] if isinstance(parsed, list) and parsed else parsed
    if not isinstance(item, dict):
        return {"ok": False, "provider": provider, "error": "返回结构异常"}

    if item.get("error"):
        err_obj = item["error"]
        msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
        return {"ok": False, "provider": provider, "error": msg or "未知错误"}

    return {"ok": True, "provider": provider, "data": item}


def normalize_codex(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "获取失败")}

    item = result["data"]
    usage = item.get("usage") or {}
    credits_obj = item.get("credits") or {}
    reset_meta = item.get("resetCredits") or {}
    reset_detail = usage.get("codexResetCredits") or {}

    cards = []
    for c in reset_detail.get("credits") or []:
        if c.get("status") == "available":
            cards.append(
                {
                    "title": c.get("title") or "重置卡",
                    "expiresAt": c.get("expires_at"),
                    "grantedAt": c.get("granted_at"),
                }
            )
    cards.sort(key=lambda x: x.get("expiresAt") or "9999")

    primary = usage.get("primary")
    secondary = usage.get("secondary")

    return {
        "ok": True,
        "plan": usage.get("loginMethod") or "Codex",
        "source": item.get("source") or "oauth",
        "updatedAt": usage.get("updatedAt") or now_iso(),
        "creditsRemaining": credits_obj.get("remaining"),
        "windows": [
            {
                "label": "5 小时",
                "usedPercent": primary.get("usedPercent") if isinstance(primary, dict) else None,
                "resetsAt": primary.get("resetsAt") if isinstance(primary, dict) else None,
                "windowMinutes": primary.get("windowMinutes") if isinstance(primary, dict) else 300,
                "inactive": primary is None,
            },
            {
                "label": "7 天",
                "usedPercent": secondary.get("usedPercent") if isinstance(secondary, dict) else None,
                "resetsAt": secondary.get("resetsAt") if isinstance(secondary, dict) else None,
                "windowMinutes": secondary.get("windowMinutes") if isinstance(secondary, dict) else 10080,
                "inactive": secondary is None,
            },
        ],
        "resetCards": {
            "available": reset_detail.get("availableCount", reset_meta.get("available", len(cards))),
            "nextExpiresAt": reset_meta.get("nextExpiresAt") or (cards[0]["expiresAt"] if cards else None),
            "items": cards,
        },
    }


def normalize_antigravity(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "获取失败")}

    item = result["data"]
    usage = item.get("usage") or {}
    extras = usage.get("extraRateWindows") or []

    label_map = {
        "antigravity-quota-summary-gemini-5h": ("Gemini", "5 小时"),
        "antigravity-quota-summary-gemini-weekly": ("Gemini", "7 天"),
        "antigravity-quota-summary-3p-5h": ("Claude / GPT", "5 小时"),
        "antigravity-quota-summary-3p-weekly": ("Claude / GPT", "7 天"),
    }

    windows = []
    for entry in extras:
        win = entry.get("window") or {}
        entry_id = entry.get("id", "")
        group, sub = label_map.get(entry_id, (entry.get("title") or "配额", ""))
        windows.append(
            {
                "id": entry_id,
                "group": group,
                "label": sub or entry.get("title") or "配额",
                "usedPercent": win.get("usedPercent"),
                "resetsAt": win.get("resetsAt"),
                "windowMinutes": win.get("windowMinutes"),
            }
        )

    if not windows:
        for key, fallback_label in (("primary", "主配额"), ("secondary", "次配额")):
            win = usage.get(key)
            if isinstance(win, dict):
                windows.append(
                    {
                        "id": key,
                        "group": fallback_label,
                        "label": fallback_label,
                        "usedPercent": win.get("usedPercent"),
                        "resetsAt": win.get("resetsAt"),
                        "windowMinutes": win.get("windowMinutes"),
                    }
                )

    return {
        "ok": True,
        "plan": usage.get("loginMethod") or "Antigravity",
        "source": item.get("source") or "oauth",
        "updatedAt": usage.get("updatedAt") or now_iso(),
        "windows": windows,
    }


class QuotaStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._refreshing = False
        self._last_trigger = 0.0
        self._state: dict[str, Any] = {
            "fetchedAt": None,
            "refreshing": False,
            "pollIntervalSeconds": POLL_INTERVAL,
            "codex": {"ok": False, "error": "正在首次拉取..."},
            "antigravity": {"ok": False, "error": "正在首次拉取..."},
        }
        self._load_cache()

    def _load_cache(self) -> None:
        if not CACHE_FILE.exists():
            return
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and "codex" in cached and "antigravity" in cached:
                cached["refreshing"] = False
                cached["pollIntervalSeconds"] = POLL_INTERVAL
                self._state = cached
        except Exception:  # noqa: BLE001
            pass

    def _save_cache(self, data: dict[str, Any]) -> None:
        try:
            tmp = CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(CACHE_FILE)
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = dict(self._state)
            snap["refreshing"] = self._refreshing
            return snap

    def refresh_async(self, force: bool = False) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._refreshing:
                return False
            if force and (now - self._last_trigger) < 15:
                return False
            self._refreshing = True
            self._last_trigger = now

        threading.Thread(target=self._do_refresh, daemon=True).start()
        return True

    def _do_refresh(self) -> None:
        results: dict[str, dict[str, Any]] = {}
        threads = []

        def worker(p: str) -> None:
            results[p] = run_codexbar(p)

        for p in PROVIDERS:
            t = threading.Thread(target=worker, args=(p,), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        codex_norm = normalize_codex(results.get("codex", {}))
        agy_norm = normalize_antigravity(results.get("antigravity", {}))

        with self._lock:
            # 某一项偶发网络失败时，保留旧数据并附带 staleError，避免画面突然变空
            if not codex_norm.get("ok") and self._state.get("codex", {}).get("ok"):
                kept = dict(self._state["codex"])
                kept["staleError"] = codex_norm.get("error")
                codex_norm = kept
            if not agy_norm.get("ok") and self._state.get("antigravity", {}).get("ok"):
                kept = dict(self._state["antigravity"])
                kept["staleError"] = agy_norm.get("error")
                agy_norm = kept

            self._state = {
                "fetchedAt": now_iso(),
                "refreshing": False,
                "pollIntervalSeconds": POLL_INTERVAL,
                "codex": codex_norm,
                "antigravity": agy_norm,
            }
            self._refreshing = False
            self._save_cache(self._state)


STORE = QuotaStore()


def poller_loop() -> None:
    STORE.refresh_async(force=False)
    while True:
        time.sleep(POLL_INTERVAL)
        STORE.refresh_async(force=False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            if not INDEX_FILE.exists():
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"index.html not found")
                return
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", INDEX_FILE.read_bytes())
            return

        if path == "/api/quota":
            payload = json.dumps(STORE.snapshot(), ensure_ascii=False).encode("utf-8")
            self._send(HTTPStatus.OK, "application/json; charset=utf-8", payload)
            return

        if path == "/manifest.webmanifest":
            manifest = {
                "name": "AI Quota Monitor",
                "short_name": "AI Quota",
                "display": "fullscreen",
                "orientation": "landscape",
                "background_color": "#000000",
                "theme_color": "#000000",
                "start_url": "/",
            }
            self._send(
                HTTPStatus.OK,
                "application/manifest+json; charset=utf-8",
                json.dumps(manifest).encode("utf-8"),
            )
            return

        self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not Found")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/refresh":
            started = STORE.refresh_async(force=True)
            payload = json.dumps({"started": started}).encode("utf-8")
            self._send(HTTPStatus.OK, "application/json; charset=utf-8", payload)
            return
        self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not Found")


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> None:
    threading.Thread(target=poller_loop, daemon=True).start()
    server = ReusableHTTPServer((HOST, PORT), Handler)
    print(f"[quota-server] listening on http://{HOST}:{PORT} (poll={POLL_INTERVAL}s)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
