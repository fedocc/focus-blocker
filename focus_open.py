#!/usr/bin/env python3
"""Controlled temporary opener for locally hosts-blocked social media on macOS."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


HOSTS_PATH = Path("/etc/hosts")
STATE_DIR = Path.home() / ".focus_open"
STATE_PATH = STATE_DIR / "state.json"
LOCK_PATH = Path("/tmp/focus_open.lock")
TEMP_PREFIX = "# TEMP_FOCUS_UNLOCK "
DEFAULT_COOLDOWN_SECONDS = 2 * 60 * 60
YOUTUBE_BUFFER_SECONDS = 2 * 60
YOUTUBE_MAX_ACCESS_SECONDS = 90 * 60
MANUAL_MAX_SECONDS = 30 * 60
DEFAULT_BROWSER_APP = "Arc"
BROWSER_GUARD_SERVICES = {"youtube", "instagram"}
MIN_REASON_CHARS = 12
MIN_REASON_WORDS = 5
BROWSER_MONITOR_GRACE_SECONDS = 12
POST_OPEN_VERIFY_SECONDS = 15
DEFAULT_URL_MONITOR_ENABLED = False
TELEGRAM_URL = "https://web.telegram.org/"
TELEGRAM_DEFAULT_MINUTES = 15
CONTENT_LIMITS = {"youtube": 2, "instagram": 1}
HELPER_PATH = Path("/Library/PrivilegedHelperTools/focus-open/focus-helper")


@dataclass(frozen=True)
class ServiceConfig:
    key: str
    display_name: str
    block_name: str
    input_prompt: str
    allowed_domains: tuple[str, ...]
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS


@dataclass(frozen=True)
class UrlValidation:
    allowed: bool
    normalized_url: str
    reason: str = ""
    access_seconds: int | None = None


class FocusOpenError(Exception):
    pass


class HostsBlockError(FocusOpenError):
    pass


SERVICES: dict[str, ServiceConfig] = {
    "telegram": ServiceConfig("telegram", "Telegram Web", "TELEGRAM", "", ("web.telegram.org",), cooldown_seconds=0),
    "youtube": ServiceConfig(
        key="youtube",
        display_name="YouTube",
        block_name="YOUTUBE",
        input_prompt="Вставьте ссылку на YouTube-видео",
        allowed_domains=("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"),
    ),
    "instagram": ServiceConfig(
        key="instagram",
        display_name="Instagram",
        block_name="INSTAGRAM",
        input_prompt="Вставьте ссылку на Instagram post/reel",
        allowed_domains=("instagram.com", "www.instagram.com"),
    ),
}


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc).astimezone()
    return parsed


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if sec or not parts:
        parts.append(f"{sec} сек")
    return " ".join(parts)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"services": {}, "history": []}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise FocusOpenError("Не удалось прочитать состояние: доступ не выдан, чтобы не сбросить лимиты.") from exc
    data.setdefault("services", {})
    data.setdefault("history", [])
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.replace(STATE_PATH)


def cooldown_remaining(last_opened_at: dt.datetime | None, now: dt.datetime, cooldown_seconds: int) -> int:
    if last_opened_at is None:
        return 0
    elapsed = (now - last_opened_at).total_seconds()
    return max(0, int(cooldown_seconds - elapsed))


def service_state(state: dict[str, Any], service_key: str) -> dict[str, Any]:
    services = state.setdefault("services", {})
    service = services.setdefault(service_key, {})
    service.setdefault("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
    return service


def normalize_url_input(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        return value
    if "://" not in value:
        value = "https://" + value
    return value


def validate_url(service_key: str, raw_url: str) -> UrlValidation:
    url = normalize_url_input(raw_url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"

    if service_key == "youtube":
        if host not in SERVICES["youtube"].allowed_domains:
            return UrlValidation(False, url, "Это не разрешенный домен YouTube.")
        if host == "youtu.be":
            video_id = path.strip("/")
            if video_id and "/" not in video_id:
                return UrlValidation(True, url)
            return UrlValidation(False, url, "Для youtu.be нужна конкретная короткая ссылка на видео.")
        if path == "/watch" and parse_qs(parsed.query).get("v", [""])[0].strip():
            return UrlValidation(True, url)
        if path.startswith("/shorts/"):
            return UrlValidation(False, url, "Shorts запрещены по умолчанию.")
        return UrlValidation(False, url, "Разрешены только youtube.com/watch?v=... и youtu.be/...")

    if service_key == "instagram":
        if host not in SERVICES["instagram"].allowed_domains:
            return UrlValidation(False, url, "Это не разрешенный домен Instagram.")
        parts = [part for part in path.split("/") if part]
        content_type = normalize_instagram_content_type(parts[0]) if parts else None
        if len(parts) >= 2 and content_type == "p":
            return UrlValidation(True, url, access_seconds=2 * 60)
        if len(parts) >= 2 and content_type == "reel":
            return UrlValidation(True, url, access_seconds=3 * 60)
        if parts and parts[0] == "stories":
            return UrlValidation(False, url, "Stories запрещены по умолчанию.")
        return UrlValidation(False, url, "Разрешены только instagram.com/p/..., instagram.com/reel/... и instagram.com/reels/...")

    return UrlValidation(False, url, f"Неизвестный сервис: {service_key}")


def normalize_instagram_content_type(value: str) -> str | None:
    if value == "p":
        return "p"
    if value in {"reel", "reels"}:
        return "reel"
    return None


def extract_youtube_video_id(raw_url: str) -> str | None:
    url = normalize_url_input(raw_url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if host == "youtu.be":
        video_id = path.strip("/").split("/", 1)[0]
        return video_id or None
    if host in SERVICES["youtube"].allowed_domains and path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0].strip()
        return video_id or None
    return None


def extract_instagram_content_key(raw_url: str) -> tuple[str, str] | None:
    url = normalize_url_input(raw_url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in SERVICES["instagram"].allowed_domains:
        return None
    parts = [part for part in (parsed.path or "/").split("/") if part]
    content_type = normalize_instagram_content_type(parts[0]) if parts else None
    if len(parts) >= 2 and content_type:
        return (content_type, parts[1])
    return None


def content_use_count(state: dict[str, Any], service: str, url: str) -> int:
    extract = extract_youtube_video_id if service == "youtube" else extract_instagram_content_key
    def identity(value: str):
        result = extract(value)
        return result[1] if service == "instagram" and result else result
    key = identity(url)
    return sum(1 for item in state.get("history", [])
               if item.get("service") == service and item.get("access_granted", True)
               and key is not None and identity(item.get("url", "")) == key)


def ensure_content_limit(state: dict[str, Any], service: str, url: str) -> None:
    limit = CONTENT_LIMITS[service]
    if content_use_count(state, service, url) >= limit:
        raise FocusOpenError(f"Эта ссылка уже использована {limit} раз. Повторный доступ закрыт.")


def telegram_used_today(state: dict[str, Any], now: dt.datetime) -> bool:
    service = state.get("services", {}).get("telegram", {})
    last = parse_iso_datetime(service.get("last_opened_at"))
    return last is not None and last.astimezone(now.tzinfo).date() == now.date()


def default_browser_app() -> str:
    script = ('ObjC.import("AppKit"); '
              '$.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL('
              '$.NSURL.URLWithString("https://web.telegram.org/")).path.js')
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 0 or not result.stdout.strip():
        raise FocusOpenError("Не удалось определить браузер по умолчанию.")
    return Path(result.stdout.strip()).stem


def run_telegram(set_minutes: int | None = None) -> int:
    with exclusive_lock():
        state = load_state()
        service = service_state(state, "telegram")
        if set_minutes is not None:
            if not 1 <= set_minutes <= 90:
                raise FocusOpenError("Продолжительность Telegram должна быть от 1 до 90 минут.")
            service["duration_minutes"] = set_minutes
            save_state(state)
            print(f"Telegram: {set_minutes} мин. со следующей сессии.")
            return 0
        now = now_local()
        active = state.get("active_unlock") or {}
        ends_at = parse_iso_datetime(active.get("ends_at"))
        if active.get("service") == "telegram" and ends_at and ends_at > now and service_block_status("telegram") == "temporarily unlocked":
            print("Telegram access is already active.")
            return 1
        if telegram_used_today(state, now):
            print("Telegram access already used today.")
            return 1
        # The existing privileged guard terminates Arc to end cached connections.
        # Refuse before granting access if the default browser has changed.
        if default_browser_app() != DEFAULT_BROWSER_APP:
            raise FocusOpenError("Для надёжной автоблокировки Telegram браузером по умолчанию должен быть Arc.")
        minutes = int(service.get("duration_minutes", TELEGRAM_DEFAULT_MINUTES))
        if not 1 <= minutes <= 90:
            raise FocusOpenError("Некорректная продолжительность Telegram. Используй telegram --set-minutes 15.")
        helper_command("check")
        # Reserve the day durably before granting access. A killed terminal cannot
        # leave an unrecorded successful session or grant a second window.
        previous = json.loads(json.dumps(state))
        append_history(state, "telegram", TELEGRAM_URL, "", minutes * 60, now, None)
        state["history"][-1]["access_granted"] = False
        state["active_unlock"] = {"service": "telegram", "url": TELEGRAM_URL,
            "started_at": now.isoformat(), "ends_at": (now + dt.timedelta(minutes=minutes)).isoformat()}
        save_state(state)
        try:
            helper_command("lease", "telegram", str(minutes * 60))
        except BaseException:
            # If grant outcome is uncertain, keep the reservation and timer.
            # A definite refusal with hosts still blocked can safely roll back.
            if service_block_status("telegram") == "blocked":
                save_state(previous)
            raise
        state["history"][-1]["access_granted"] = True
        save_state(state)
        open_url(TELEGRAM_URL)  # macOS default browser; cookies/profile untouched.
        print(f"Telegram unlocked for {minutes} minutes.")
    return 0


def browser_url_allowed(service_key: str, expected_url: str, current_url: str) -> bool:
    validation = validate_url(service_key, current_url)
    if not validation.allowed:
        return False
    if service_key == "youtube":
        return extract_youtube_video_id(expected_url) == extract_youtube_video_id(current_url)
    if service_key == "instagram":
        return extract_instagram_content_key(expected_url) == extract_instagram_content_key(current_url)
    return normalize_url_input(expected_url).rstrip("/") == validation.normalized_url.rstrip("/")


def is_transient_browser_url(raw_url: str) -> bool:
    value = raw_url.strip().lower()
    return (
        not value
        or value == "about:blank"
        or value.startswith("arc://")
        or value.startswith("chrome://")
        or value.startswith("edge://")
    )


def browser_monitor_decision(service_key: str, expected_url: str, current_url: str, within_grace: bool) -> str:
    if browser_url_allowed(service_key, expected_url, current_url):
        return "allow"
    if within_grace and is_transient_browser_url(current_url):
        return "wait"
    return "block"


def run_osascript_dialog(script: str) -> str | None:
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def prompt_text(message: str) -> str:
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display dialog "{escaped}" default answer "" buttons {{"Отмена", "OK"}} default button "OK"\ntext returned of result'
    answer = run_osascript_dialog(script)
    if answer is not None:
        return answer.strip()
    return input(f"{message}: ").strip()


def confirm(message: str) -> bool:
    answer = input(f"{message} [y/N]: ").strip().lower()
    return answer in {"y", "yes", "д", "да"}


def should_use_browser_guard(service_key: str, disabled: bool = False) -> bool:
    return not disabled and service_key in BROWSER_GUARD_SERVICES


def should_reopen_browser_after_guard(browser_guard: bool, was_running: bool, disabled: bool = False) -> bool:
    return browser_guard and was_running and not disabled


def reason_is_valid(reason: str) -> bool:
    words = re.findall(r"\S+", reason.strip())
    return len(reason.strip()) >= MIN_REASON_CHARS and len(words) >= MIN_REASON_WORDS


def get_reason() -> str:
    reason = prompt_text("Зачем ты открываешь это? Кратко опиши цель.")
    if not reason_is_valid(reason):
        raise FocusOpenError(f"Причина слишком короткая. Нужно минимум {MIN_REASON_WORDS} слов и {MIN_REASON_CHARS} символов.")
    return reason.strip()


def get_youtube_duration_seconds(url: str) -> int | None:
    if shutil.which("yt-dlp") is None:
        print("yt-dlp не найден. Установить можно командой: brew install yt-dlp", file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            ["yt-dlp", "--no-warnings", "--print", "duration", url],
            text=True,
            capture_output=True,
            check=False,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Не удалось запустить yt-dlp: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"yt-dlp не смог получить длительность: {result.stderr.strip()}", file=sys.stderr)
        return None
    match = re.search(r"\d+(?:\.\d+)?", result.stdout.strip())
    if not match:
        return None
    return max(1, int(float(match.group(0))))


def get_youtube_duration_with_temporary_hosts_unlock(url: str) -> int | None:
    try:
        helper_command("lease", "youtube", "60")
        return get_youtube_duration_seconds(url)
    finally:
        restore_all_hosts_blocks()


def ask_manual_duration_seconds(max_seconds: int = MANUAL_MAX_SECONDS) -> int:
    raw = input(f"Укажи длительность вручную в минутах, максимум {max_seconds // 60}: ").strip()
    try:
        minutes = float(raw.replace(",", "."))
    except ValueError as exc:
        raise FocusOpenError("Не удалось прочитать длительность в минутах.") from exc
    seconds = int(minutes * 60)
    if seconds <= 0 or seconds > max_seconds:
        raise FocusOpenError(f"Ручная длительность должна быть от 1 секунды до {format_duration(max_seconds)}.")
    return seconds


def compute_access_seconds(service_key: str, url: str, validation: UrlValidation, youtube_duration: int | None = None) -> int:
    if service_key == "instagram":
        return validation.access_seconds or 2 * 60
    if service_key != "youtube":
        raise FocusOpenError(f"Неизвестный сервис: {service_key}")

    duration = youtube_duration
    if duration is None:
        duration = 10 * 60
    access_seconds = duration + YOUTUBE_BUFFER_SECONDS
    return min(access_seconds, 6 * 60 * 60)


def find_block_bounds(lines: list[str], block_name: str) -> tuple[int, int]:
    start_marker = f"# === FOCUS_BLOCK:{block_name} START ==="
    end_marker = f"# === FOCUS_BLOCK:{block_name} END ==="
    start = end = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == start_marker:
            start = index
        elif stripped == end_marker and start is not None:
            end = index
            break
    if start is None or end is None or end <= start:
        raise HostsBlockError(f"В /etc/hosts не найден корректный блок FOCUS_BLOCK:{block_name}. Файл не изменен.")
    return start, end


def transform_hosts_block(hosts_text: str, block_name: str, unlock: bool) -> str:
    keep_trailing_newline = hosts_text.endswith("\n")
    lines = hosts_text.splitlines()
    start, end = find_block_bounds(lines, block_name)
    changed = list(lines)
    for index in range(start + 1, end):
        line = changed[index]
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if unlock:
            if stripped.startswith(TEMP_PREFIX) or stripped.startswith("#") or not stripped:
                continue
            if re.match(r"^(127\.0\.0\.1|0\.0\.0\.0|::1)\s+\S+", stripped):
                changed[index] = f"{indent}{TEMP_PREFIX}{stripped}"
        else:
            if stripped.startswith(TEMP_PREFIX):
                changed[index] = indent + stripped[len(TEMP_PREFIX) :]
    output = "\n".join(changed)
    if keep_trailing_newline:
        output += "\n"
    return output


def restore_all_hosts_text(hosts_text: str) -> str:
    output = hosts_text
    for config in SERVICES.values():
        try:
            output = transform_hosts_block(output, config.block_name, unlock=False)
        except HostsBlockError:
            continue
    return output


def read_hosts() -> str:
    return HOSTS_PATH.read_text(encoding="utf-8")


def helper_command(*args: str) -> None:
    command_args = args[:2] if args and args[0] == "lease" else args
    duration_input = args[2] if args and args[0] == "lease" else ""
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", str(HELPER_PATH), *command_args],
        input=duration_input,
        text=True, capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        raise FocusOpenError(
            "Системный помощник недоступен. Нужна однократная установка Focus Open. "
            + result.stderr.strip()
        )


def update_hosts_for_service(service_key: str, unlock: bool, seconds: int = 60) -> None:
    if unlock:
        helper_command("lease", service_key, str(seconds))
    else:
        helper_command("restore")


def restore_all_hosts_blocks() -> bool:
    changed = TEMP_PREFIX in read_hosts()
    helper_command("restore")
    return changed


def flush_dns() -> None:
    # The privileged helper flushes DNS together with each hosts change.
    pass


@contextlib.contextmanager
def exclusive_lock():
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FocusOpenError("Уже идет временная разблокировка. Дождись завершения или запусти restore.") from exc
        yield


def set_active_unlock(state: dict[str, Any], service_key: str | None, url: str | None = None) -> None:
    if service_key is None:
        state.pop("active_unlock", None)
    else:
        state["active_unlock"] = {"service": service_key, "url": url, "started_at": now_local().isoformat()}
    save_state(state)


def append_history(
    state: dict[str, Any],
    service_key: str,
    url: str,
    reason: str,
    duration_seconds: int,
    opened_at: dt.datetime,
    closed_at: dt.datetime | None,
) -> None:
    state.setdefault("history", []).append(
        {
            "service": service_key,
            "url": url,
            "reason": reason,
            "duration_seconds": duration_seconds,
            "opened_at": opened_at.isoformat(),
            "closed_at": closed_at.isoformat() if closed_at else None,
        }
    )
    service = service_state(state, service_key)
    service["last_opened_at"] = opened_at.isoformat()
    service["cooldown_seconds"] = SERVICES[service_key].cooldown_seconds
    save_state(state)


def app_is_running(app_name: str) -> bool:
    script = f'application "{app_name}" is running'
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            timeout=5,
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def quit_app(app_name: str) -> bool:
    script = f'tell application "{app_name}" to quit'
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            timeout=5,
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def wait_until_app_not_running(app_name: str, timeout_seconds: int = 20) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not app_is_running(app_name):
            return True
        time.sleep(0.5)
    return not app_is_running(app_name)


def get_browser_active_url(app_name: str) -> str | None:
    script = f'''
tell application "{app_name}"
    if not running then return ""
    if (count of windows) is 0 then return ""
    try
        return URL of active tab of front window
    on error
        return ""
    end try
end tell
'''
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            timeout=5,
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def prepare_browser_guard(app_name: str) -> None:
    if not app_is_running(app_name):
        return
    if not quit_app(app_name) or not wait_until_app_not_running(app_name):
        raise FocusOpenError(f"Не удалось аккуратно закрыть {app_name}. Закрой его вручную и повтори команду.")


def cleanup_browser_guard(app_name: str) -> None:
    if not app_is_running(app_name):
        return
    if not quit_app(app_name) or not wait_until_app_not_running(app_name):
        print(f"Warning: не удалось закрыть {app_name}. Блокировка hosts все равно будет восстановлена.", file=sys.stderr)


def open_app(app_name: str) -> None:
    result = subprocess.run(["/usr/bin/open", "-a", app_name], check=False)
    if result.returncode != 0:
        print(f"Warning: не удалось открыть {app_name} обратно.", file=sys.stderr)


def open_url(url: str, browser_app: str | None = None) -> None:
    command = ["/usr/bin/open"]
    if browser_app:
        command.extend(["-a", browser_app])
    command.append(url)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise FocusOpenError("Не удалось открыть ссылку через /usr/bin/open.")


def sleep_with_countdown(seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while True:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            return
        time.sleep(min(1, remaining))


def sleep_with_browser_monitor(seconds: int, service_key: str, expected_url: str, browser_app: str) -> None:
    deadline = time.monotonic() + seconds
    grace_deadline = time.monotonic() + BROWSER_MONITOR_GRACE_SECONDS
    last_seen_url = None
    while True:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            return
        active_url = get_browser_active_url(browser_app)
        if active_url:
            last_seen_url = active_url
            decision = browser_monitor_decision(service_key, expected_url, active_url, time.monotonic() < grace_deadline)
            if decision == "wait":
                time.sleep(1)
                continue
            if decision == "block":
                print(f"Навигация ушла с разрешенной ссылки: {active_url}", file=sys.stderr)
                cleanup_browser_guard(browser_app)
                raise FocusOpenError("Доступ остановлен: активная вкладка открыла неразрешенный URL.")
        elif last_seen_url is not None and not app_is_running(browser_app):
            return
        time.sleep(min(1, remaining))


def wait_for_allowed_browser_url(service_key: str, expected_url: str, browser_app: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_url = None
    while time.monotonic() < deadline:
        active_url = get_browser_active_url(browser_app)
        if active_url:
            last_url = active_url
            decision = browser_monitor_decision(service_key, expected_url, active_url, within_grace=True)
            if decision == "allow":
                return
            if decision == "block":
                raise FocusOpenError(f"{browser_app} открыл неразрешенную страницу вместо нужной ссылки: {active_url}")
        time.sleep(0.5)
    raise FocusOpenError(f"{browser_app} не открыл нужную ссылку за {timeout_seconds} сек. Последний URL: {last_url or 'unknown'}")


def ensure_cooldown_allows(state: dict[str, Any], service_key: str, current_time: dt.datetime) -> None:
    service = service_state(state, service_key)
    last = parse_iso_datetime(service.get("last_opened_at"))
    cooldown = int(service.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))
    remaining = cooldown_remaining(last, current_time, cooldown)
    if remaining > 0:
        available_at = current_time + dt.timedelta(seconds=remaining)
        raise FocusOpenError(
            f"Cooldown активен. Осталось {format_duration(remaining)}. "
            f"Можно снова не раньше: {available_at.isoformat(timespec='seconds')}"
        )


def final_summary(
    config: ServiceConfig,
    url: str,
    reason: str,
    access_seconds: int,
    current_time: dt.datetime,
) -> str:
    next_at = current_time + dt.timedelta(seconds=config.cooldown_seconds)
    return (
        f"Сервис: {config.display_name}\n"
        f"Ссылка: {url}\n"
        f"Причина: {reason}\n"
        f"Время доступа: {format_duration(access_seconds)}\n"
        f"Следующее использование будет доступно не раньше: {next_at.isoformat(timespec='seconds')}"
    )


def run_service(
    service_key: str,
    raw_url: str | None = None,
    browser_app: str = DEFAULT_BROWSER_APP,
    disable_browser_guard: bool = False,
    url_monitor_enabled: bool = DEFAULT_URL_MONITOR_ENABLED,
    disable_browser_reopen: bool = False,
) -> int:
    config = SERVICES[service_key]
    with exclusive_lock():
        state = load_state()
        current_time = now_local()
        active = state.get("active_unlock") or {}
        if active.get("service") == "telegram" and service_block_status("telegram") == "temporarily unlocked":
            raise FocusOpenError("Telegram access is already active. Дождись завершения сессии.")

        url = raw_url.strip() if raw_url else prompt_text(config.input_prompt)
        validation = validate_url(service_key, url)
        if not validation.allowed:
            raise FocusOpenError(validation.reason)
        url = validation.normalized_url
        ensure_content_limit(state, service_key, url)
        ensure_cooldown_allows(state, service_key, current_time)

        helper_command("check")
        if browser_app != DEFAULT_BROWSER_APP or disable_browser_guard:
            raise FocusOpenError("Автоматический таймер поддерживает только Arc с browser guard.")
        reason = ""

        browser_guard = should_use_browser_guard(service_key, disable_browser_guard)
        browser_prepared = False
        browser_was_running_before_guard = app_is_running(browser_app) if browser_guard else False
        youtube_duration = None
        if service_key == "youtube" and shutil.which("yt-dlp") is not None:
            if browser_guard:
                prepare_browser_guard(browser_app)
                browser_prepared = True
            youtube_duration = get_youtube_duration_with_temporary_hosts_unlock(url)

        access_seconds = compute_access_seconds(service_key, url, validation, youtube_duration=youtube_duration)

        opened_at = now_local()
        append_history(state, service_key, url, reason, access_seconds, opened_at, None)
        state["history"][-1]["access_granted"] = False
        save_state(state)
        opened = False
        try:
            if browser_guard and not browser_prepared:
                prepare_browser_guard(browser_app)
            update_hosts_for_service(service_key, unlock=True, seconds=access_seconds)
            state["history"][-1]["access_granted"] = True
            save_state(state)
            flush_dns()
            set_active_unlock(state, service_key, url)
            opened = True
            open_url(url, browser_app if browser_guard else None)
            if browser_guard and url_monitor_enabled:
                wait_for_allowed_browser_url(service_key, url, browser_app, POST_OPEN_VERIFY_SECONDS)
                sleep_with_browser_monitor(access_seconds, service_key, url, browser_app)
            else:
                sleep_with_countdown(access_seconds)
        finally:
            restore_error: Exception | None = None
            try:
                restore_all_hosts_blocks()
                flush_dns()
                if should_reopen_browser_after_guard(browser_guard, browser_was_running_before_guard, disable_browser_reopen):
                    open_app(browser_app)
            except Exception as exc:
                restore_error = exc
                print(
                    "КРИТИЧЕСКАЯ ОШИБКА: автоматическое восстановление /etc/hosts не удалось. "
                    "Запусти: python3 ~/.local/bin/focus_open.py restore",
                    file=sys.stderr,
                )
                print(f"Причина: {exc}", file=sys.stderr)
            state = load_state()
            if restore_error is None:
                set_active_unlock(state, None)
            history = state.get("history", [])
            if history and restore_error is None:
                history[-1]["closed_at"] = now_local().isoformat()
                save_state(state)
            if restore_error is not None:
                raise FocusOpenError("Не удалось автоматически восстановить блокировку.") from restore_error
    return 0


def service_block_status(service_key: str) -> str:
    config = SERVICES[service_key]
    try:
        text = read_hosts()
        lines = text.splitlines()
        start, end = find_block_bounds(lines, config.block_name)
    except (OSError, HostsBlockError):
        return "block missing"
    block = lines[start + 1 : end]
    unlocked = any(line.lstrip().startswith(TEMP_PREFIX) for line in block)
    blocked = any(re.match(r"^\s*(127\.0\.0\.1|0\.0\.0\.0|::1)\s+\S+", line) for line in block)
    if unlocked:
        return "temporarily unlocked"
    if blocked:
        return "blocked"
    return "no blocking rows"


def print_status() -> int:
    state = load_state()
    current_time = now_local()
    active = state.get("active_unlock")
    if active and active.get("service") in SERVICES and service_block_status(active["service"]) != "temporarily unlocked":
        active = None
    print(f"Active unlock: {active if active else 'none'}")
    for key, config in SERVICES.items():
        service = service_state(state, key)
        last = parse_iso_datetime(service.get("last_opened_at"))
        cooldown = int(service.get("cooldown_seconds", config.cooldown_seconds))
        remaining = cooldown_remaining(last, current_time, cooldown)
        if key == "telegram":
            availability = "already used today" if telegram_used_today(state, current_time) else "available today"
        elif remaining:
            availability = f"cooldown {format_duration(remaining)} left"
        else:
            availability = "available now"
        print(f"{config.display_name}: {availability}; hosts: {service_block_status(key)}")
    return 0


def hosts_flags() -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/stat", "-f", "%Sf", str(HOSTS_PATH)],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip()


def app_exists(app_name: str) -> bool:
    return Path("/Applications", f"{app_name}.app").exists() or Path.home().joinpath("Applications", f"{app_name}.app").exists()


def print_doctor() -> int:
    print("Focus Open doctor")
    print(f"script: {Path(__file__)}")
    print(f"state: {STATE_PATH}")
    print(f"yt-dlp: {shutil.which('yt-dlp') or 'not installed'}")
    print("yt-dlp install: brew install yt-dlp")

    flags = hosts_flags()
    print(f"/etc/hosts flags: {flags or '-'}")
    if "uchg" in flags or "schg" in flags:
        print("warning: /etc/hosts immutable flag is set; run: sudo chflags nouchg /etc/hosts")
    for key, config in SERVICES.items():
        print(f"{config.display_name}: hosts {service_block_status(key)}")

    print(f"{DEFAULT_BROWSER_APP}.app: {'found' if app_exists(DEFAULT_BROWSER_APP) else 'not found'}")
    print(f"{DEFAULT_BROWSER_APP} running: {app_is_running(DEFAULT_BROWSER_APP)}")
    print(f"browser guard services: {', '.join(sorted(BROWSER_GUARD_SERVICES))}")
    print(f"url monitor default: {DEFAULT_URL_MONITOR_ENABLED}")
    return 0


def restore_command() -> int:
    with exclusive_lock():
        changed = restore_all_hosts_blocks()
        flush_dns()
        state = load_state()
        set_active_unlock(state, None)
    print("Временные разблокировки восстановлены." if changed else "Временных разблокировок не найдено.")
    return 0


def print_install_zsh() -> int:
    script_path = Path.home() / ".local/bin/focus_open.py"
    print("Добавь в ~/.zshrc:")
    print(f"alias focus-open='python3 {script_path}'")
    print(f"alias youtube='python3 {script_path} youtube'")
    print(f"alias telegram='python3 {script_path} telegram'")
    print(f"alias instagram='python3 {script_path} instagram'")
    print(f"alias social-open='python3 {script_path} open'")
    return 0


def run_self_tests() -> int:
    test_path = Path(__file__).with_name("test_focus_open.py")
    result = subprocess.run([sys.executable, str(test_path)], check=False)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Temporarily open focused social links blocked via /etc/hosts.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    telegram_parser = subparsers.add_parser("telegram")
    telegram_parser.add_argument("--set-minutes", type=int, metavar="MINUTES")
    youtube_parser = subparsers.add_parser("youtube")
    youtube_parser.add_argument("--browser-app", default=DEFAULT_BROWSER_APP)
    youtube_parser.add_argument("--no-browser-guard", action="store_true")
    youtube_parser.add_argument("--no-browser-reopen", action="store_true")
    youtube_parser.add_argument("--url-monitor", action="store_true")

    instagram_parser = subparsers.add_parser("instagram")
    instagram_parser.add_argument("--browser-app", default=DEFAULT_BROWSER_APP)
    instagram_parser.add_argument("--no-browser-guard", action="store_true")
    instagram_parser.add_argument("--no-browser-reopen", action="store_true")
    instagram_parser.add_argument("--url-monitor", action="store_true")

    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("--service", required=True, choices=sorted(CONTENT_LIMITS))
    open_parser.add_argument("--url", required=True)
    open_parser.add_argument("--browser-app", default=DEFAULT_BROWSER_APP)
    open_parser.add_argument("--no-browser-guard", action="store_true")
    open_parser.add_argument("--no-browser-reopen", action="store_true")
    open_parser.add_argument("--url-monitor", action="store_true")

    subparsers.add_parser("status")
    subparsers.add_parser("doctor")
    subparsers.add_parser("restore")
    subparsers.add_parser("install-zsh")
    subparsers.add_parser("self-test")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "telegram":
            return run_telegram(args.set_minutes)
        if args.command in CONTENT_LIMITS:
            return run_service(
                args.command,
                browser_app=args.browser_app,
                disable_browser_guard=args.no_browser_guard,
                url_monitor_enabled=args.url_monitor,
                disable_browser_reopen=args.no_browser_reopen,
            )
        if args.command == "open":
            return run_service(
                args.service,
                args.url,
                browser_app=args.browser_app,
                disable_browser_guard=args.no_browser_guard,
                url_monitor_enabled=args.url_monitor,
                disable_browser_reopen=args.no_browser_reopen,
            )
        if args.command == "status":
            return print_status()
        if args.command == "doctor":
            return print_doctor()
        if args.command == "restore":
            return restore_command()
        if args.command == "install-zsh":
            return print_install_zsh()
        if args.command == "self-test":
            return run_self_tests()
    except KeyboardInterrupt:
        print("\nПрервано. Если доступ уже был открыт, запускаю восстановление...", file=sys.stderr)
        with contextlib.suppress(Exception):
            restore_all_hosts_blocks()
            flush_dns()
        return 130
    except FocusOpenError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except PermissionError:
        print("Ошибка: нет прав на чтение или запись /etc/hosts. Запусти команду снова и введи пароль sudo.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
