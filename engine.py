"""Ghép ngẫu nhiên các video khác nhau thành nhiều file kết quả."""

from __future__ import annotations

import ctypes
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".wmv",
    ".flv",
    ".mpeg",
    ".mpg",
    ".3gp",
    ".ts",
    ".mts",
    ".m2ts",
}

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def silence_windows_crash_dialogs() -> None:
    """Broken FFmpeg builds can pop a modal DLL dialog. Suppress that for child processes."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except Exception:
        pass


@dataclass(frozen=True)
class Tools:
    ffmpeg: str
    ffprobe: str


@dataclass(frozen=True)
class OutputPlan:
    video_count: int
    clips_per_output: int
    requested: int
    maximum: int
    actual: int
    error: str | None = None
    notice: str | None = None


@dataclass
class BatchResult:
    created: list[Path]
    failures: list[str]
    cancelled: bool
    manifest: Path | None


def fmt_count(value: int) -> str:
    if value < 0:
        return str(value)
    if value < 1_000_000_000_000_000:
        return f"{value:,}".replace(",", ".")
    mantissa, exponent = f"{value:.4e}".split("e+")
    return f"{mantissa} × 10^{int(exponent)}"


def maximum_result_count(video_count: int, clips_per_output: int) -> int:
    """Mỗi video chỉ dùng một lần, nên số kết quả tối đa là số video chia cho số clip mỗi kết quả."""
    if clips_per_output < 1 or video_count < clips_per_output:
        return 0
    return video_count // clips_per_output


def sample_sequences(item_count: int, pick: int, count: int, rng: random.Random | None = None) -> list[tuple[int, ...]]:
    maximum = maximum_result_count(item_count, pick)
    if count < 1 or count > maximum:
        raise ValueError("Số kết quả nằm ngoài số cách ghép có thể tạo.")
    picker = rng or random.SystemRandom()
    order = list(range(item_count))
    picker.shuffle(order)
    sequences = [tuple(order[start : start + pick]) for start in range(0, count * pick, pick)]
    _validate_sequences(sequences, item_count, pick)
    return sequences


def _validate_sequences(sequences: list[tuple[int, ...]], item_count: int, pick: int) -> None:
    usage = [0] * item_count
    for sequence in sequences:
        if len(sequence) != pick or len(set(sequence)) != pick:
            raise ValueError("Mỗi kết quả phải gồm các video khác nhau.")
        for item in sequence:
            if item < 0 or item >= item_count or usage[item]:
                raise ValueError("Một video nguồn bị dùng hơn 1 lần.")
            usage[item] = 1


def plan_outputs(video_count: int, clips_per_output: int, requested: int) -> OutputPlan:
    if video_count < 2:
        return OutputPlan(
            video_count=video_count,
            clips_per_output=clips_per_output,
            requested=requested,
            maximum=0,
            actual=0,
            error=(
                f"Cần ít nhất 2 video khác nhau trong thư mục nguồn để ghép. "
                f"Hiện có {fmt_count(video_count)} video."
            ),
        )
    if clips_per_output < 2:
        return OutputPlan(
            video_count=video_count,
            clips_per_output=clips_per_output,
            requested=requested,
            maximum=0,
            actual=0,
            error="Mỗi video kết quả phải ghép ít nhất 2 video khác nhau.",
        )
    if requested < 1:
        return OutputPlan(
            video_count=video_count,
            clips_per_output=clips_per_output,
            requested=requested,
            maximum=0,
            actual=0,
            error="Số kết quả muốn xuất phải từ 1 trở lên.",
        )
    if clips_per_output > video_count:
        return OutputPlan(
            video_count=video_count,
            clips_per_output=clips_per_output,
            requested=requested,
            maximum=0,
            actual=0,
            error=(
                f"Không ghép được vì mỗi kết quả cần {fmt_count(clips_per_output)} video khác nhau, "
                f"trong khi thư mục chỉ có {fmt_count(video_count)} video. "
                f"Hãy nhập số video mỗi kết quả từ 2 đến {fmt_count(video_count)}."
            ),
        )

    maximum = maximum_result_count(video_count, clips_per_output)
    if requested > maximum:
        notice = (
            f"Bạn muốn xuất {fmt_count(requested)} kết quả. "
            f"Mỗi video nguồn chỉ được dùng đúng 1 lần, nên với {fmt_count(video_count)} video "
            f"và mỗi kết quả ghép {fmt_count(clips_per_output)} video chỉ tạo được tối đa {fmt_count(maximum)} kết quả. "
            f"App sẽ chỉ tạo {fmt_count(maximum)} video."
        )
        return OutputPlan(
            video_count=video_count,
            clips_per_output=clips_per_output,
            requested=requested,
            maximum=maximum,
            actual=maximum,
            notice=notice,
        )
    return OutputPlan(
        video_count=video_count,
        clips_per_output=clips_per_output,
        requested=requested,
        maximum=maximum,
        actual=requested,
    )


def list_videos(folder: Path, recursive: bool) -> list[Path]:
    if not folder.is_dir():
        return []
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    found: list[Path] = []
    for path in iterator:
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in VIDEO_EXTS:
            continue
        try:
            if path.stat().st_size <= 0:
                continue
        except OSError:
            continue
        found.append(path)
    found.sort(key=lambda item: str(item).casefold())
    unique: list[Path] = []
    seen: set[tuple] = set()
    for path in found:
        key = _file_identity(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def display_name(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return path.name


def _bundled_ffmpeg_dirs() -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent)
    return roots


def find_tools() -> tuple[Tools | None, str | None]:
    silence_windows_crash_dialogs()
    for root in _bundled_ffmpeg_dirs():
        ffmpeg = root / "ffmpeg.exe"
        probe = root / "ffprobe.exe"
        if ffmpeg.is_file() and probe.is_file() and _supports_libx264(str(ffmpeg)):
            return Tools(ffmpeg=str(ffmpeg), ffprobe=str(probe)), None
    candidates: list[str] = []
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    if sys.platform == "win32":
        try:
            listed = _capture(["where.exe", "ffmpeg"], 10)
        except (OSError, subprocess.TimeoutExpired):
            listed = None
        if listed and listed.returncode == 0:
            for line in listed.stdout.splitlines():
                path = line.strip()
                if path and path not in candidates:
                    candidates.append(path)
    candidates.sort(key=_ffmpeg_rank)
    for ffmpeg in candidates:
        if not _supports_libx264(ffmpeg):
            continue
        probe = Path(ffmpeg).with_name("ffprobe.exe")
        if not probe.exists():
            probe = Path(ffmpeg).with_name("ffprobe")
        if probe.exists():
            return Tools(ffmpeg=ffmpeg, ffprobe=str(probe)), None
    if not candidates:
        return None, "Không tìm thấy FFmpeg. Hãy cài FFmpeg (có libx264) rồi mở lại app."
    return None, "Đã thấy FFmpeg nhưng bản này không có libx264 hoặc không có ffprobe đi kèm."


def output_filename(index: int, total: int, width: int, height: int) -> str:
    digits = max(3, len(str(total)))
    prefix = "doc" if height > width else "ngang"
    return f"{prefix}_{index:0{digits}d}.mp4"


def render_job(
    *,
    videos: list[Path],
    source_root: Path,
    clips_per_output: int,
    count: int,
    output_dir: Path,
    width: int,
    height: int,
    size_label: str,
    tools: Tools,
    cancel: threading.Event,
    on_log: Callable[[str], None],
    on_progress: Callable[[int, int, str], None],
    notice: str | None = None,
) -> BatchResult:
    on_log("Đang trộn ngẫu nhiên các video nguồn...")
    sequences = sample_sequences(len(videos), clips_per_output, count)
    on_log(
        f"Đã chọn {fmt_count(len(sequences))} kết quả. "
        "Cách ghép và thứ tự video đều ngẫu nhiên. "
        "Mỗi video nguồn chỉ được dùng đúng 1 lần."
    )
    leftover = len(videos) - len(sequences) * clips_per_output
    if leftover:
        on_log(f"Còn {fmt_count(leftover)} video không đủ để ghép thêm một kết quả nên không dùng.")
    return _render_sequences(
        videos=videos,
        source_root=source_root,
        sequences=sequences,
        output_dir=output_dir,
        width=width,
        height=height,
        size_label=size_label,
        tools=tools,
        cancel=cancel,
        on_log=on_log,
        on_progress=on_progress,
        notice=notice,
    )


def _ffmpeg_rank(path: str) -> tuple[int, str]:
    lowered = path.lower()
    risky = any(token in lowered for token in ("miniconda", "anaconda", "latentsync"))
    return (1 if risky else 0, lowered)


def _capture(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Read child output as UTF-8. Windows locale (cp1252) cannot decode Japanese names."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
    )


def _supports_libx264(ffmpeg: str) -> bool:
    silence_windows_crash_dialogs()
    try:
        proc = _capture([ffmpeg, "-hide_banner", "-encoders"], 8)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "libx264" in proc.stdout


def _run_command(args: list[str], cancel: threading.Event) -> tuple[int, str]:
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=NO_WINDOW,
    )
    chunks: list[bytes] = []

    def read_stderr() -> None:
        if proc.stderr is not None:
            chunks.append(proc.stderr.read())

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    while proc.poll() is None:
        if cancel.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            break
        try:
            proc.wait(timeout=0.15)
        except subprocess.TimeoutExpired:
            continue
    reader.join(timeout=5)
    code = proc.returncode if proc.returncode is not None else -1
    if cancel.is_set():
        code = -2
    error = _decode_bytes(b"".join(chunks)).strip()
    return code, error


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "cp932", "cp1258"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    has_audio: bool
    duration: float
    video_codec: str
    pix_fmt: str
    fps: float
    audio_codec: str
    sample_rate: str
    channels: int


@dataclass(frozen=True)
class PreparedClip:
    path: Path
    info: VideoInfo
    kept_original: bool


def orientation_matches(source_width: int, source_height: int, target_width: int, target_height: int) -> bool:
    """Đúng khổ khi video nguồn và khung xuất cùng là dọc, hoặc cùng là ngang."""
    if min(source_width, source_height, target_width, target_height) <= 0:
        return False
    if source_width == source_height:
        return False
    return (source_height > source_width) == (target_height > target_width)


def _file_identity(path: Path) -> tuple:
    try:
        stat = path.stat()
        resolved = str(path.resolve()).casefold()
    except OSError:
        return ("path", str(path).casefold())
    if stat.st_ino:
        return ("inode", stat.st_dev, stat.st_ino)
    return ("path", resolved)


def _fps_value(rate: str) -> float:
    if not rate or rate in {"0/0", "N/A"}:
        return 0.0
    try:
        if "/" in rate:
            numerator, denominator = rate.split("/", 1)
            den = float(denominator)
            if den == 0:
                return 0.0
            return float(numerator) / den
        return float(rate)
    except ValueError:
        return 0.0


def _rotation_degrees(stream: dict) -> int:
    tags = stream.get("tags") or {}
    raw = tags.get("rotate")
    if raw is not None:
        try:
            return int(float(raw)) % 360
        except ValueError:
            pass
    for side in stream.get("side_data_list") or []:
        if "rotation" not in side:
            continue
        try:
            return int(float(side["rotation"])) % 360
        except (TypeError, ValueError):
            continue
    return 0


def _display_size(stream: dict) -> tuple[int, int]:
    try:
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (TypeError, ValueError):
        return 0, 0
    sar = str(stream.get("sample_aspect_ratio") or "1:1")
    if ":" in sar:
        numerator, denominator = sar.split(":", 1)
        try:
            num_i = int(numerator)
            den_i = int(denominator)
        except ValueError:
            num_i, den_i = 1, 1
        if num_i > 0 and den_i > 0 and not (num_i == 1 and den_i == 1):
            width = max(1, int(round(width * num_i / den_i)))
    if _rotation_degrees(stream) in {90, 270}:
        width, height = height, width
    return width, height


def _probe_video(path: Path, tools: Tools) -> VideoInfo | None:
    try:
        proc = _capture(
            [
                tools.ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    stdout = proc.stdout or ""
    if proc.returncode != 0 or not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    streams = data.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video is None:
        return None
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    width, height = _display_size(video)
    if width <= 0 or height <= 0:
        return None
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    channels = 0
    if audio is not None:
        try:
            channels = int(audio.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0
    return VideoInfo(
        width=width,
        height=height,
        has_audio=audio is not None,
        duration=duration,
        video_codec=str(video.get("codec_name") or ""),
        pix_fmt=str(video.get("pix_fmt") or ""),
        fps=_fps_value(str(video.get("avg_frame_rate") or "")),
        audio_codec=str(audio.get("codec_name") or "") if audio else "",
        sample_rate=str(audio.get("sample_rate") or "") if audio else "",
        channels=channels,
    )


def _same_stream(left: VideoInfo, right: VideoInfo) -> bool:
    if (
        left.width != right.width
        or left.height != right.height
        or left.video_codec != right.video_codec
        or left.pix_fmt != right.pix_fmt
        or left.has_audio != right.has_audio
    ):
        return False
    if abs(left.fps - right.fps) > 0.08:
        return False
    if not left.has_audio:
        return True
    return (
        left.audio_codec == right.audio_codec
        and left.sample_rate == right.sample_rate
        and left.channels == right.channels
    )


def _can_copy(clips: list[PreparedClip]) -> bool:
    if len(clips) < 2:
        return False
    return all(_same_stream(clips[0].info, clip.info) for clip in clips[1:])


def _orientation_name(width: int, height: int) -> str:
    if height > width:
        return "dọc"
    if width > height:
        return "ngang"
    return "vuông"


def _has_audio(path: Path, tools: Tools) -> bool:
    try:
        proc = _capture(
            [
                tools.ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return bool((proc.stdout or "").strip())


def _video_filter(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps=30,format=yuv420p"
    )


def _normalize(
    src: Path,
    dst: Path,
    width: int,
    height: int,
    with_audio: bool,
    tools: Tools,
    cancel: threading.Event,
) -> tuple[bool, str]:
    video_filter = _video_filter(width, height)
    common_tail = [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-shortest",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    if with_audio:
        args = [
            tools.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            video_filter,
            "-af",
            "apad",
            *common_tail,
        ]
    else:
        args = [
            tools.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-vf",
            video_filter,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            *common_tail,
        ]
    code, error = _run_command(args, cancel)
    if code == -2:
        _remove_file(dst)
        return False, "Đã dừng"
    if code == 0 and dst.exists() and dst.stat().st_size > 0:
        return True, ""
    _remove_file(dst)
    return False, _short_error(error, code)


def _concat(files: list[Path], list_file: Path, dst: Path, tools: Tools, cancel: threading.Event) -> tuple[bool, str]:
    lines = []
    for file in files:
        escaped = file.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code, error = _run_command(
        [
            tools.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        cancel,
    )
    if code == -2:
        _remove_file(dst)
        return False, "Đã dừng"
    if code == 0 and dst.exists() and dst.stat().st_size > 0:
        return True, ""
    _remove_file(dst)
    return False, _short_error(error, code)


def _concat_fitted(
    clips: list[PreparedClip],
    dst: Path,
    width: int,
    height: int,
    work_dir: Path,
    tools: Tools,
    cancel: threading.Event,
) -> tuple[bool, str]:
    graph: list[str] = []
    labels: list[str] = []
    for index, clip in enumerate(clips):
        duration = clip.info.duration if clip.info.duration > 0.05 else 0.1
        graph.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
        )
        if clip.info.has_audio:
            graph.append(
                f"[{index}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"aresample=async=1:first_pts=0,apad,atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[a{index}]"
            )
        else:
            graph.append(
                f"anullsrc=r=44100:cl=stereo,atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[a{index}]"
            )
        labels.append(f"[v{index}][a{index}]")
    graph.append(f"{''.join(labels)}concat=n={len(clips)}:v=1:a=1[outv][outa]")
    script = work_dir / f"graph_{dst.stem}.txt"
    script.write_text(";".join(graph), encoding="utf-8")
    code, error = _run_command(
        [
            tools.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            *[part for clip in clips for part in ("-i", str(clip.path))],
            "-filter_complex_script",
            str(script),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        cancel,
    )
    if code == -2:
        _remove_file(dst)
        return False, "Đã dừng"
    if code == 0 and dst.exists() and dst.stat().st_size > 0:
        return True, ""
    _remove_file(dst)
    return False, _short_error(error, code)


def _join_clips(
    clips: list[PreparedClip],
    list_file: Path,
    dst: Path,
    width: int,
    height: int,
    work_dir: Path,
    tools: Tools,
    cancel: threading.Event,
    on_log: Callable[[str], None],
) -> tuple[bool, str]:
    if _can_copy(clips):
        ok, error = _concat([clip.path for clip in clips], list_file, dst, tools, cancel)
        if ok or error == "Đã dừng":
            return ok, error
        on_log("Không ghép trực tiếp được, chuyển sang encode lại bản ghép.")
    return _concat_fitted(clips, dst, width, height, work_dir, tools, cancel)


def _short_error(error: str, code: int) -> str:
    clean = " ".join(error.split())
    if not clean:
        return f"FFmpeg dừng với mã {code}."
    if len(clean) > 500:
        clean = clean[-500:]
    return clean


def _remove_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _unique_destination(directory: Path, filename: str, reserved: set[Path]) -> Path:
    candidate = directory / filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    number = 2
    while candidate.resolve() in reserved or candidate.exists():
        candidate = directory / f"{stem}_{number}{suffix}"
        number += 1
    return candidate


def _write_manifest(
    path: Path,
    *,
    source_root: Path,
    output_dir: Path,
    size_label: str,
    clips_per_output: int,
    notice: str | None,
    created: list[tuple[Path, tuple[str, ...]]],
    failures: list[str],
    cancelled: bool,
) -> None:
    lines = [
        "Ghép video ngẫu nhiên",
        "Mỗi video nguồn chỉ dùng đúng 1 lần. Cách ghép và thứ tự đều ngẫu nhiên.",
        f"Khổ xuất: {size_label}",
        f"Số video mỗi kết quả: {clips_per_output}",
        f"Số video đã tạo: {len(created)}",
        f"Thư mục nguồn: {source_root}",
        f"Thư mục xuất: {output_dir}",
        "",
    ]
    if notice:
        lines.extend(["Ghi chú:", notice, ""])
    if cancelled:
        lines.extend(["Trạng thái: đã dừng trước khi ghép hết.", ""])
    for index, (output, parts) in enumerate(created, start=1):
        lines.append(f"{index}. {output.name}")
        for part in parts:
            lines.append(f"   - {part}")
        lines.append("")
    if failures:
        lines.append("Không ghép được:")
        lines.extend(f"- {item}" for item in failures)
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8-sig")


def _render_sequences(
    *,
    videos: list[Path],
    source_root: Path,
    sequences: list[tuple[int, ...]],
    output_dir: Path,
    width: int,
    height: int,
    size_label: str,
    tools: Tools,
    cancel: threading.Event,
    on_log: Callable[[str], None],
    on_progress: Callable[[int, int, str], None],
    notice: str | None,
) -> BatchResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    names = [display_name(path, source_root) for path in videos]
    used: list[int] = []
    seen: set[int] = set()
    for sequence in sequences:
        for index in sequence:
            if index not in seen:
                seen.add(index)
                used.append(index)

    total_steps = len(used) + len(sequences)
    done_steps = 0
    created: list[tuple[Path, tuple[str, ...]]] = []
    failures: list[str] = []
    reserved = {path.resolve() for path in videos}
    manifest_path = output_dir / "danh_sach_ghep.txt"
    cancelled = False
    prepared: dict[int, PreparedClip] = {}
    target_kind = _orientation_name(width, height)

    def publish(label: str) -> None:
        on_progress(done_steps, total_steps, label)

    def save_manifest() -> None:
        _write_manifest(
            manifest_path,
            source_root=source_root,
            output_dir=output_dir,
            size_label=size_label,
            clips_per_output=len(sequences[0]) if sequences else 0,
            notice=notice,
            created=created,
            failures=failures,
            cancelled=cancelled,
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="ghep_video_"))
    on_log(
        f"Kiểm tra khổ của {len(used)} video. "
        f"Video đã đúng khổ {target_kind} được giữ nguyên."
    )
    try:
        kept_count = 0
        changed_count = 0
        for position, index in enumerate(used, start=1):
            if cancel.is_set():
                cancelled = True
                break
            label = f"Kiểm tra {position}/{len(used)}"
            publish(label)
            info = _probe_video(videos[index], tools)
            if info is not None and orientation_matches(info.width, info.height, width, height):
                prepared[index] = PreparedClip(videos[index], info, True)
                kept_count += 1
                on_log(f"Giữ nguyên {names[index]}: đã là video {target_kind}.")
            else:
                source_kind = _orientation_name(info.width, info.height) if info is not None else "không rõ"
                on_log(f"Chuẩn hóa {names[index]}: đang là video {source_kind}, cần xuất {target_kind}.")
                destination = temp_dir / f"clip_{index:04d}.mp4"
                has_audio = info.has_audio if info is not None else _has_audio(videos[index], tools)
                ok, error = _normalize(videos[index], destination, width, height, has_audio, tools, cancel)
                if not ok and error != "Đã dừng":
                    on_log(f"Thử lại không tiếng: {names[index]}")
                    ok, error = _normalize(videos[index], destination, width, height, False, tools, cancel)
                if cancel.is_set() or error == "Đã dừng":
                    cancelled = True
                    break
                if not ok:
                    message = f"Không xử lý được {names[index]}: {error}"
                    failures.append(message)
                    on_log(message)
                else:
                    fitted = _probe_video(destination, tools)
                    if fitted is None and info is not None:
                        fitted = VideoInfo(
                            width=width,
                            height=height,
                            has_audio=True,
                            duration=info.duration,
                            video_codec="h264",
                            pix_fmt="yuv420p",
                            fps=30.0,
                            audio_codec="aac",
                            sample_rate="44100",
                            channels=2,
                        )
                    if fitted is None:
                        message = f"Không đọc được video sau khi chuẩn hóa: {names[index]}"
                        failures.append(message)
                        on_log(message)
                    else:
                        prepared[index] = PreparedClip(destination, fitted, False)
                        changed_count += 1
            done_steps += 1
            publish(label)
        if not cancelled:
            on_log(f"Giữ nguyên {kept_count} video. Chuẩn hóa {changed_count} video khác khổ.")

        if not cancelled:
            for position, sequence in enumerate(sequences, start=1):
                if cancel.is_set():
                    cancelled = True
                    break
                label = f"Ghép {position}/{len(sequences)}"
                publish(label)
                part_names = tuple(names[index] for index in sequence)
                identities = [_file_identity(videos[index]) for index in sequence]
                if len(set(sequence)) != len(sequence) or len(set(identities)) != len(identities):
                    message = f"Bỏ qua kết quả {position} vì có video bị lặp trong cùng một kết quả."
                    failures.append(message)
                    on_log(message)
                    done_steps += 1
                    publish(label)
                    continue
                missing = [names[index] for index in sequence if index not in prepared]
                if missing:
                    message = f"Bỏ qua kết quả {position} vì lỗi video: {', '.join(missing)}"
                    failures.append(message)
                    on_log(message)
                    done_steps += 1
                    publish(label)
                    continue
                pieces = [prepared[index] for index in sequence]
                filename = output_filename(position, len(sequences), width, height)
                destination = _unique_destination(output_dir, filename, reserved)
                on_log(f"{label}: {destination.name}  ←  {' + '.join(part_names)}")
                ok, error = _join_clips(
                    pieces,
                    temp_dir / f"list_{position:04d}.txt",
                    destination,
                    width,
                    height,
                    temp_dir,
                    tools,
                    cancel,
                    on_log,
                )
                if cancel.is_set() or error == "Đã dừng":
                    cancelled = True
                    break
                if ok:
                    created.append((destination, part_names))
                    reserved.add(destination.resolve())
                    save_manifest()
                else:
                    message = f"Không ghép được kết quả {position}: {error}"
                    failures.append(message)
                    on_log(message)
                done_steps += 1
                publish(label)
        if cancelled:
            on_log("Đã dừng theo yêu cầu.")
        save_manifest()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    publish("Xong")
    return BatchResult(
        created=[item[0] for item in created],
        failures=failures,
        cancelled=cancelled,
        manifest=manifest_path if manifest_path.exists() else None,
    )
