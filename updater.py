"""Kiểm tra bản phát hành trên GitHub và thay file exe đang chạy."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

APP_VERSION = "1.1.0"
REPO = "luanvo1910/randomVideo"
ASSET_NAME = "GhepVideo.exe"
USER_AGENT = "GhepVideo"


@dataclass(frozen=True)
class Release:
    version: str
    download_url: str
    notes: str


def parse_version(value: str) -> tuple[int, ...]:
    text = value.strip()
    if text.lower().startswith("v"):
        text = text[1:]
    parts: list[int] = []
    for piece in text.split("."):
        number = ""
        for char in piece:
            if char.isdigit():
                number += char
            else:
                break
        if not number:
            break
        parts.append(int(number))
    return tuple(parts) if parts else (0,)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def fetch_latest(timeout: float = 20) -> Release | None:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tag = str(payload.get("tag_name") or "")
    if not tag:
        return None
    download_url = ""
    for asset in payload.get("assets") or []:
        if asset.get("name") == ASSET_NAME and asset.get("browser_download_url"):
            download_url = str(asset["browser_download_url"])
            break
    if not download_url:
        return None
    return Release(version=tag.lstrip("vV"), download_url=download_url, notes=str(payload.get("body") or ""))


def download_release(url: str, destination: Path, on_progress, cancel, timeout: float = 120) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            with temporary.open("wb") as handle:
                while True:
                    if cancel.is_set():
                        raise InterruptedError("Đã hủy cập nhật.")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    on_progress(received, total)
        if temporary.stat().st_size < 1_000_000:
            raise OSError("File tải về quá nhỏ, không phải bản cài đặt.")
        temporary.replace(destination)
    except Exception:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
        raise


def launch_replacer(new_file: Path, target: Path) -> None:
    """Đợi app thoát, ghi đè exe, rồi mở lại. Script chạy tách khỏi tiến trình hiện tại."""
    script = Path(tempfile.gettempdir()) / "ghepvideo-update.ps1"
    source = str(new_file)
    dest = str(target)
    script.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$source = '{source.replace(chr(39), chr(39) * 2)}'",
                f"$dest = '{dest.replace(chr(39), chr(39) * 2)}'",
                "$deadline = (Get-Date).AddMinutes(2)",
                "while ((Get-Process -Name 'GhepVideo' -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {",
                "    Start-Sleep -Milliseconds 400",
                "}",
                "if (Get-Process -Name 'GhepVideo' -ErrorAction SilentlyContinue) { exit 1 }",
                "Move-Item -LiteralPath $source -Destination $dest -Force",
                "Start-Process -FilePath $dest",
                "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
            ]
        ),
        encoding="utf-8-sig",
    )
    creation = 0
    if sys.platform == "win32":
        creation = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [
            "cmd",
            "/c",
            "start",
            "",
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
        ],
        creationflags=creation,
        close_fds=True,
        cwd=str(target.parent),
    )


def running_as_exe() -> bool:
    return bool(getattr(sys, "frozen", False)) and os.name == "nt"
