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
from zoneinfo import ZoneInfo


HOSTS_PATH = Path("/etc/hosts")
STATE_DIR = Path.home() / ".focus_open"
STATE_PATH = STATE_DIR / "state.json"
LOCK_PATH = Path("/tmp/focus_open.lock")
TEMP_PREFIX = "# TEMP_FOCUS_UNLOCK "
DEFAULT_COOLDOWN_SECONDS = 2 * 60 * 60
YOUTUBE_COOLDOWN_SECONDS = 6 * 60 * 60
YOUTUBE_BUFFER_SECONDS = 2 * 60
YOUTUBE_METADATA_TIMEOUT_SECONDS = 12
YOUTUBE_MAX_ACCESS_SECONDS = 90 * 60
MANUAL_MAX_SECONDS = 30 * 60
DEFAULT_BROWSER_APP = "Arc"
BROWSER_GUARD_SERVICES = {"youtube", "instagram", "x"}
MIN_REASON_CHARS = 12
MIN_REASON_WORDS = 5
BROWSER_MONITOR_GRACE_SECONDS = 12
POST_OPEN_VERIFY_SECONDS = 15
DEFAULT_URL_MONITOR_ENABLED = False
TELEGRAM_URL = "https://web.telegram.org/"
TELEGRAM_DEFAULT_MINUTES = 7
INSTAGRAM_URL = "https://www.instagram.com/"
INSTAGRAM_ACCESS_SECONDS = 2 * 60
X_URL = "https://x.com/"
DAILY_TIMEZONE = ZoneInfo("Europe/Moscow")
SHORT_SOCIAL_HOMES = {"instagram": INSTAGRAM_URL, "x": X_URL}
CONTENT_LIMITS = {"youtube": 2}
HELPER_PATH = Path("/Library/PrivilegedHelperTools/focus-open/focus-helper")
YOUTUBE_POLICY_PATH = HELPER_PATH.parent / "youtube-policy.json"


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
        cooldown_seconds=YOUTUBE_COOLDOWN_SECONDS,
    ),
    "instagram": ServiceConfig(
        key="instagram",
        display_name="Instagram",
        block_name="INSTAGRAM",
        input_prompt="",
        allowed_domains=("instagram.com", "www.instagram.com"),
        cooldown_seconds=0,
    ),
    "x": ServiceConfig("x", "X", "X", "", ("x.com", "www.x.com", "twitter.com", "www.twitter.com"), cooldown_seconds=0),
}

PERMANENT_BLOCKS: dict[str, ServiceConfig] = {
    "vkvideo": ServiceConfig(
        key="vkvideo",
        display_name="VK Video",
        block_name="VKVIDEO",
        input_prompt="",
        allowed_domains=(
            "vkvideo.ru",
            "www.vkvideo.ru",
            "m.vkvideo.ru",
            "api.vkvideo.ru",
            "live.vkvideo.ru",
            "api.live.vkvideo.ru",
            "video.vk.com",
        ),
        cooldown_seconds=0,
    ),
}
ALL_BLOCKS = {**SERVICES, **PERMANENT_BLOCKS}


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
    service.setdefault("cooldown_seconds", SERVICES[service_key].cooldown_seconds)
    return service


def normalize_url_input(raw_url: str) -> str:
    value = raw_url.strip()
    markdown = re.fullmatch(r"\[[^\]\n]*\]\((https?://[^\s]+)\)", value)
    if markdown:
        value = markdown.group(1)
    elif value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
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

    if service_key in SHORT_SOCIAL_HOMES:
        if host not in SERVICES[service_key].allowed_domains:
            return UrlValidation(False, url, "Это не разрешенный домен " + SERVICES[service_key].display_name + ".")
        return UrlValidation(True, url, access_seconds=INSTAGRAM_ACCESS_SECONDS)

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
    limit = CONTENT_LIMITS.get(service)
    if limit is None:
        return
    if content_use_count(state, service, url) >= limit:
        raise FocusOpenError(f"Эта ссылка уже использована {limit} раз. Повторный доступ закрыт.")


def service_used_today(state: dict[str, Any], key: str, now: dt.datetime) -> bool:
    last = parse_iso_datetime(state.get("services", {}).get(key, {}).get("last_opened_at"))
    return last is not None and now.astimezone(DAILY_TIMEZONE).date() <= last.astimezone(DAILY_TIMEZONE).date()


def telegram_used_today(state: dict[str, Any], now: dt.datetime) -> bool:
    return service_used_today(state, "telegram", now)


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
            if set_minutes != TELEGRAM_DEFAULT_MINUTES:
                raise FocusOpenError("Защищённый лимит Telegram — 7 минут в день.")
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
        minutes = TELEGRAM_DEFAULT_MINUTES
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
    if service_key in SHORT_SOCIAL_HOMES:
        return True
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
    executable = shutil.which("yt-dlp")
    validation = validate_url("youtube", url)
    if executable is None or not validation.allowed:
        return None
    try:
        result = subprocess.run(
            [executable, "--ignore-config", "--no-warnings", "--no-playlist",
             "--skip-download", "--ignore-no-formats-error",
             "--socket-timeout", "3", "--retries", "0", "--extractor-retries", "0",
             "--print", "duration", "--", validation.normalized_url],
            text=True,
            capture_output=True,
            check=False,
            timeout=YOUTUBE_METADATA_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Metadata is optional. Keep the established quiet 12-minute fallback.
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        return None
    return max(1, int(float(value)))


def get_youtube_duration_with_temporary_hosts_unlock(url: str) -> int | None:
    raise FocusOpenError("Метаданные YouTube доступны только внутри сессии выбранного видео.")


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
    if service_key in SHORT_SOCIAL_HOMES:
        return INSTAGRAM_ACCESS_SECONDS
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
    for config in ALL_BLOCKS.values():
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


def helper_json(command: str, argument: str | None = None, payload: Any = None) -> dict[str, Any]:
    args = ["/usr/bin/sudo", "-n", str(HELPER_PATH), command]
    if argument is not None:
        args.append(argument)
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else "",
                            text=True, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise FocusOpenError(result.stderr.strip() or "Системный контроль YouTube недоступен.")
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise FocusOpenError("Системный помощник вернул некорректный ответ.") from exc


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
    if browser_app == DEFAULT_BROWSER_APP and (urlparse(url).hostname or "") in SERVICES["youtube"].allowed_domains:
        if urlparse(url).path == "/playlist":
            playlist_ids = parse_qs(urlparse(url).query).get("list", [])
            for item in youtube_policy()["playlists"]:
                if playlist_ids == [item["id"]]:
                    url = youtube_course_entry(item)
                    break
        # Arc's external-link/Little Arc path may create a tab whose URL is
        # not visible to automation until selected. Use its native main window.
        quoted = json.dumps(url, ensure_ascii=False)
        script = '\n'.join([
            'tell application "Arc"',
            'activate',
            'repeat 20 times',
            'if (count of windows) > 0 then exit repeat',
            'delay 0.25',
            'end repeat',
            'if (count of windows) is 0 then error "Открой главное окно Arc"',
            'set knownIds to {}',
            'repeat with t in tabs of front window',
            'set end of knownIds to (id of t) as text',
            'end repeat',
            'tell front window to make new tab with properties {URL:' + quoted + '}',
            'set didOpen to false',
            'repeat with t in tabs of front window',
            'if ((id of t) as text) is not in knownIds then',
            'set URL of t to ' + quoted,
            'select t',
            'set didOpen to true',
            'exit repeat',
            'end if',
            'end repeat',
            'if not didOpen then error "Не удалось открыть контролируемую вкладку YouTube"',
            'return "OK"',
            'end tell',
        ])
        result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=15)
        if result.returncode or result.stdout.strip() != "OK":
            raise FocusOpenError(result.stderr.strip() or "Не удалось открыть контролируемую вкладку Arc.")
        return
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
    if service_used_today(state, service_key, current_time):
        raise FocusOpenError(SERVICES[service_key].display_name + " уже открывался сегодня. Следующее открытие — после полуночи по Москве.")


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


def youtube_policy() -> dict[str, Any]:
    try:
        value = json.loads(YOUTUBE_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FocusOpenError("Защищённые учебные плейлисты ещё не установлены.") from exc
    if value.get("schema_version") != 1:
        raise FocusOpenError("Неизвестный формат списка учебных плейлистов.")
    return value


def print_youtube_return_links(policy: dict[str, Any]) -> None:
    for playlist in policy["playlists"]:
        print(f"Учебный плейлист: {playlist['title']}")
        print(youtube_course_entry(playlist))
        print(f"Вернуться к лекциям: youtube --course {playlist.get('alias', playlist['id'])}")
    for channel in policy.get('channels', []):
        print(f"Учебный канал: {channel['title']}")
        print(youtube_channel_entry(channel))
        print(f"Открыть канал: youtube --course {channel.get('alias', channel['id'])}")


def youtube_course_entry(playlist: dict[str, Any]) -> str:
    return f"https://www.youtube.com/watch?v={playlist['videos'][0]['id']}&list={playlist['id']}"


def youtube_channel_entry(channel: dict[str, Any]) -> str:
    return f"https://www.youtube.com/channel/{channel['id']}/videos"


def youtube_target(raw_url: str | None, course: str | None, policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    playlists = policy["playlists"]
    if course:
        for channel in policy.get('channels', []):
            if course in {channel['id'], channel.get('alias')}:
                return (youtube_channel_entry(channel), {'channel_id': channel['id']})
        for playlist in playlists:
            if course in {playlist["id"], playlist.get("alias")}:
                return (youtube_course_entry(playlist), {"playlist_id": playlist["id"]})
        raise FocusOpenError("Такой учебный плейлист не добавлен.")
    if not raw_url:
        print("Лекции из белого списка доступны постоянно.")
        print_youtube_return_links(policy)
        raw_url = prompt_text(SERVICES["youtube"].input_prompt)
    url = normalize_url_input(raw_url)
    parsed = urlparse(url)
    if parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        for channel in policy.get('channels', []):
            for prefix in ('/channel/' + channel['id'], '/@' + channel['handle'].lower()):
                path = parsed.path.rstrip('/')
                if path.startswith('/@'):
                    path = path.lower()
                if path in {prefix + suffix for suffix in ('', '/featured', '/videos', '/streams', '/shorts', '/playlists')}:
                    return (youtube_channel_entry(channel), {'channel_id': channel['id']})
        if parsed.path.startswith('/shorts/'):
            video_id = parsed.path[len('/shorts/'):].strip('/')
            uploads = {c['uploads_playlist_id'] for c in policy.get('channels', [])}
            channel_video_ids = {v['id'] for p in playlists if p['id'] in uploads for v in p['videos']}
            if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) and video_id in channel_video_ids:
                return (f'https://www.youtube.com/watch?v={video_id}', {'video_id': video_id})
    if parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"} and parsed.path == "/playlist":
        ids = parse_qs(parsed.query).get("list", [])
        if len(ids) != 1:
            raise FocusOpenError("Нужна ссылка на один разрешённый плейлист.")
        return youtube_target(None, ids[0], policy)
    validation = validate_url("youtube", url)
    video_id = extract_youtube_video_id(url)
    if not validation.allowed or not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise FocusOpenError(validation.reason or "Нужна корректная ссылка на конкретное видео YouTube.")
    # Canonicalize away arbitrary playlist parameters: the root service checks actual membership.
    return (f"https://www.youtube.com/watch?v={video_id}", {"video_id": video_id, "seconds": 12 * 60})


def prepare_youtube_browser() -> None:
    # Restore Arc's windows while hosts are still blocked. The root helper can
    # then remove stale YouTube tabs before it grants the requested scope.
    script = '\n'.join([
        'tell application "Arc"', 'activate', 'repeat 20 times',
        'if (count of windows) > 0 then exit repeat', 'delay 0.25', 'end repeat',
        'if (count of windows) is 0 then error "Открой главное окно Arc"',
        'return "OK"', 'end tell',
    ])
    result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=15)
    if result.returncode or result.stdout.strip() != "OK":
        raise FocusOpenError(result.stderr.strip() or "Не удалось подготовить окно Arc.")


def run_youtube(raw_url: str | None = None, course: str | None = None,
                browser_app: str = DEFAULT_BROWSER_APP, disable_browser_guard: bool = False) -> int:
    if browser_app != DEFAULT_BROWSER_APP or disable_browser_guard:
        raise FocusOpenError("Контроль видео YouTube обязателен; открытие начинается в Arc.")
    policy = youtube_policy()
    url, request = youtube_target(raw_url, course, policy)
    # Print before opening: these links remain in terminal scrollback even
    # when navigation immediately revokes access or the browser fails to open.
    print(f"Открываемая ссылка: {url}")
    print_youtube_return_links(policy)
    with exclusive_lock():
        state = load_state()
        study_ids = {v['id'] for p in policy['playlists'] for v in p['videos']}
        study_playlists = {p['id'] for p in policy['playlists']}
        study_channels = {c['id'] for c in policy.get('channels', [])}
        is_study = request.get("video_id") in study_ids or request.get("playlist_id") in study_playlists or request.get('channel_id') in study_channels
        if is_study:
            helper_json("guard-check")
            open_url(url, browser_app)
            print("Лекции из белого списка доступны постоянно, без таймера.")
            return 0
        root_status = helper_json("youtube-status")
        if root_status.get("lease"):
            raise FocusOpenError("Уже идёт временная сессия. Дождись её завершения.")
        helper_json("guard-check")
        prepare_youtube_browser()
        previous = json.loads(json.dumps(state))
        grant = helper_json("lease", "youtube", request)
        scope = grant["scope"]
        opened_at = now_local()
        seconds = max(1, int(grant["deadline"] - opened_at.timestamp()))
        append_history(state, "youtube", url, "Учебный плейлист" if scope["mode"] == "playlist" else "", seconds, opened_at, None)
        if scope["mode"] == "playlist":
            # Course viewing must not reset the ordinary-video cooldown.
            state["services"]["youtube"] = previous.get("services", {}).get("youtube", {})
        state["history"][-1]["access_granted"] = True
        state["history"][-1]["youtube_mode"] = scope["mode"]
        state["active_unlock"] = {"service": "youtube", "url": url, "started_at": opened_at.isoformat(),
                                  "ends_at": dt.datetime.fromtimestamp(grant["deadline"], opened_at.tzinfo).isoformat(),
                                  "youtube_mode": scope["mode"]}
        try:
            save_state(state)
            open_url(url, browser_app)
            if scope["mode"] == "video":
                duration = get_youtube_duration_seconds(url)
                if duration is not None:
                    total = min(duration + YOUTUBE_BUFFER_SECONDS, 6 * 60 * 60)
                    updated = helper_json("youtube-duration", payload=total)
                    state["history"][-1]["duration_seconds"] = total
                    state["active_unlock"]["ends_at"] = dt.datetime.fromtimestamp(updated["deadline"], opened_at.tzinfo).isoformat()
                    save_state(state)
        except BaseException:
            restore_all_hosts_blocks()
            state.pop("active_unlock", None)
            state["history"][-1]["closed_at"] = now_local().isoformat()
            save_state(state)
            raise
        ends_at = parse_iso_datetime(state["active_unlock"]["ends_at"])
        print("YouTube: только выбранное видео")
        print(f"Автоблокировка: {ends_at.strftime('%H:%M')}.")
        print("Посторонний ролик закрывается. Доступ к лекциям из белого списка сохраняется.")
    return 0


def run_service(
    service_key: str,
    raw_url: str | None = None,
    browser_app: str = DEFAULT_BROWSER_APP,
    disable_browser_guard: bool = False,
    url_monitor_enabled: bool = DEFAULT_URL_MONITOR_ENABLED,
    disable_browser_reopen: bool = False,
) -> int:
    if service_key == "youtube":
        return run_youtube(raw_url, browser_app=browser_app, disable_browser_guard=disable_browser_guard)
    if service_key not in SHORT_SOCIAL_HOMES:
        raise FocusOpenError("Неизвестный сервис.")
    homepage = SHORT_SOCIAL_HOMES[service_key]
    if browser_app != DEFAULT_BROWSER_APP or disable_browser_guard:
        raise FocusOpenError("Контроль вкладок обязателен; открытие начинается в Arc.")
    with exclusive_lock():
        state = load_state()
        current_time = now_local()
        ensure_cooldown_allows(state, service_key, current_time)
        helper_command("check")
        helper_json("guard-check")
        # No browser restart: the daemon expires this window and closes only
        # Instagram tabs, preserving lectures and unrelated browser work.
        helper_command("lease", service_key, str(INSTAGRAM_ACCESS_SECONDS))
        append_history(state, service_key, homepage, "", INSTAGRAM_ACCESS_SECONDS, current_time, None)
        state["history"][-1]["access_granted"] = True
        state["active_unlock"] = {"service": service_key, "url": homepage,
                                  "started_at": current_time.isoformat(),
                                  "ends_at": (current_time + dt.timedelta(seconds=INSTAGRAM_ACCESS_SECONDS)).isoformat()}
        save_state(state)
        open_url(homepage, browser_app)
        print(SERVICES[service_key].display_name + " открыт на 2 минуты. Следующий доступ — после полуночи по Москве.")
    return 0


def service_block_status(service_key: str) -> str:
    config = ALL_BLOCKS[service_key]
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
        availability = "уже использован сегодня" if service_used_today(state, key, current_time) else "доступен сегодня"
        print(f"{config.display_name}: {availability}; hosts: {service_block_status(key)}")
    for key, config in PERMANENT_BLOCKS.items():
        print(f"{config.display_name}: permanently blocked; hosts: {service_block_status(key)}")
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
    for key, config in PERMANENT_BLOCKS.items():
        print(f"{config.display_name}: permanent hosts {service_block_status(key)}")

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
    print(f"alias x='python3 {script_path} x'")
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
    youtube_parser.add_argument("--course", metavar="COURSE")
    youtube_parser.add_argument("--courses", action="store_true")
    youtube_parser.add_argument("--browser-app", default=DEFAULT_BROWSER_APP)
    youtube_parser.add_argument("--no-browser-guard", action="store_true")
    youtube_parser.add_argument("--no-browser-reopen", action="store_true")
    youtube_parser.add_argument("--url-monitor", action="store_true")

    for key in ("instagram", "x"):
        social_parser = subparsers.add_parser(key)
        social_parser.add_argument("--browser-app", default=DEFAULT_BROWSER_APP)
        social_parser.add_argument("--no-browser-guard", action="store_true")
        social_parser.add_argument("--no-browser-reopen", action="store_true")
        social_parser.add_argument("--url-monitor", action="store_true")

    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("--service", required=True, choices=sorted(BROWSER_GUARD_SERVICES))
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
        if args.command == "youtube":
            if args.courses:
                for item in youtube_policy()["playlists"]:
                    print(f"{item.get('alias', item['id'])}: {item['title']} ({len(item['videos'])} видео)")
                print_youtube_return_links(youtube_policy())
                return 0
            return run_youtube(course=args.course, browser_app=args.browser_app, disable_browser_guard=args.no_browser_guard)
        if args.command in BROWSER_GUARD_SERVICES:
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
