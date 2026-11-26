#!/usr/bin/env python3
"""
监听 macOS 锁屏/解锁与休眠唤醒：
1. 自动控制安卓手机息屏/亮屏、跳过滑动锁屏
2. 防止长时间不操作自动降亮度（调大 screen_off_timeout + stayon）
3. 维护 adb reverse 端口转发，并在无线 ADB 断连或报 Operation not permitted 时自动重启 adb daemon 重连
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import time
from datetime import datetime

ADB_BIN = os.environ.get("ADB_BIN") or shutil.which("adb") or "/opt/homebrew/bin/adb"
PORT = os.environ.get("QUOTA_PORT", "8787")
FIXED_SERIAL = os.environ.get("ADB_SERIAL", "").strip()
WIRELESS_TARGET = os.environ.get("ADB_WIRELESS_TARGET", "").strip()
CHECK_INTERVAL = float(os.environ.get("SYNC_INTERVAL", "2.0"))
RECONNECT_COOLDOWN = 20.0
KILL_SERVER_COOLDOWN = 60.0

_cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
_cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

_cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
_cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
_cf.CFStringCreateWithCString.restype = ctypes.c_void_p
_cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_cf.CFDictionaryGetValue.restype = ctypes.c_void_p
_cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
_cf.CFBooleanGetValue.restype = ctypes.c_bool
_cf.CFRelease.argtypes = [ctypes.c_void_p]

_KEY_LOCKED = _cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", 0x08000100)
_KEY_ON_CONSOLE = _cf.CFStringCreateWithCString(None, b"kCGSSessionOnConsoleKey", 0x08000100)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [screen-sync] {msg}", flush=True)


def is_mac_locked() -> bool:
    d = _cg.CGSessionCopyCurrentDictionary()
    if not d:
        return False
    try:
        locked_val = _cf.CFDictionaryGetValue(d, _KEY_LOCKED)
        if locked_val and _cf.CFBooleanGetValue(locked_val):
            return True
        console_val = _cf.CFDictionaryGetValue(d, _KEY_ON_CONSOLE)
        if console_val and not _cf.CFBooleanGetValue(console_val):
            return True
        return False
    finally:
        _cf.CFRelease(d)


def run_adb_raw(args: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [ADB_BIN, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return proc.returncode, out
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def resolve_serial() -> str | None:
    if FIXED_SERIAL:
        return FIXED_SERIAL
    code, out = run_adb_raw(["devices"], timeout=5.0)
    if code != 0:
        return None
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def try_reconnect_wireless(last_kill_ts: float) -> tuple[str | None, float]:
    if not WIRELESS_TARGET:
        return None, last_kill_ts

    code, out = run_adb_raw(["connect", WIRELESS_TARGET], timeout=8.0)
    if code == 0 and ("connected to" in out or "already connected" in out):
        serial = resolve_serial()
        if serial:
            log(f"无线 ADB 重连成功: {serial}")
            return serial, last_kill_ts

    now = time.monotonic()
    need_restart = (
        "Operation not permitted" in out
        or "cannot connect" in out
        or "failed to connect" in out
        or code != 0
    )
    if need_restart and (now - last_kill_ts) >= KILL_SERVER_COOLDOWN:
        log(f"无线 ADB 连接失败（{out.splitlines()[0] if out else code}），尝试重启 adb server...")
        last_kill_ts = now
        run_adb_raw(["kill-server"], timeout=8.0)
        time.sleep(0.8)
        run_adb_raw(["start-server"], timeout=10.0)
        time.sleep(0.8)
        c2, o2 = run_adb_raw(["connect", WIRELESS_TARGET], timeout=8.0)
        if c2 == 0 and ("connected to" in o2 or "already connected" in o2):
            serial = resolve_serial()
            if serial:
                log(f"重启 adb server 后重连成功: {serial}")
                return serial, last_kill_ts
        log(f"重启 adb server 后仍未连上: {o2.splitlines()[0] if o2 else c2}")

    return None, last_kill_ts


def adb(serial: str, args: list[str]) -> bool:
    code, out = run_adb_raw(["-s", serial, *args], timeout=8.0)
    if code != 0:
        log(f"adb 失败({code}): {' '.join(args)} -> {out}")
        return False
    return True


def wake_and_tunnel(serial: str) -> None:
    adb(serial, ["reverse", f"tcp:{PORT}", f"tcp:{PORT}"])
    # 防止供电常亮时触发系统超时前的自动降亮度（Dim）
    adb(serial, ["shell", "svc", "power", "stayon", "true"])
    adb(serial, ["shell", "settings", "put", "system", "screen_off_timeout", "2147483647"])
    adb(serial, ["shell", "settings", "put", "system", "dim_screen", "0"])
    adb(serial, ["shell", "input", "keyevent", "224"])
    adb(serial, ["shell", "wm", "dismiss-keyguard"])
    log(f"已同步亮屏、防降亮度、跳过锁屏并刷新 adb reverse (tcp:{PORT}) -> {serial}")


def sleep_phone(serial: str) -> None:
    adb(serial, ["shell", "input", "keyevent", "223"])
    log(f"已发送手机息屏指令 (keyevent 223) -> {serial}")


def main() -> None:
    log(f"启动完成，端口 tcp:{PORT}，无线目标 {WIRELESS_TARGET or '无'}，轮询 {CHECK_INTERVAL}s")
    last_locked: bool | None = None
    last_serial: str | None = None
    last_wall = time.time()
    last_reconnect_try = 0.0
    last_kill_ts = 0.0

    while True:
        now_wall = time.time()
        woke_from_sleep = (now_wall - last_wall) > (CHECK_INTERVAL * 5)
        last_wall = now_wall

        locked = is_mac_locked()
        serial = resolve_serial()

        if serial is None:
            if last_serial is not None:
                log("ADB 设备已断开，等待自动重连...")
            last_serial = None
            now_mono = time.monotonic()
            if (now_mono - last_reconnect_try) >= RECONNECT_COOLDOWN:
                last_reconnect_try = now_mono
                serial, last_kill_ts = try_reconnect_wireless(last_kill_ts)

        if serial is not None:
            reconnected = serial != last_serial
            if reconnected:
                log(f"检测到 ADB 设备: {serial}")
                last_serial = serial
                if locked:
                    sleep_phone(serial)
                else:
                    wake_and_tunnel(serial)
            elif woke_from_sleep:
                log("检测到 Mac 休眠唤醒")
                if not locked:
                    wake_and_tunnel(serial)
            elif last_locked is not None and locked != last_locked:
                if locked:
                    log("检测到 Mac 锁屏")
                    sleep_phone(serial)
                else:
                    log("检测到 Mac 解锁")
                    wake_and_tunnel(serial)

        last_locked = locked
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
