"""Video download command."""

from __future__ import annotations

import os
import re
import shutil
import tempfile

import click

from . import common


def _sanitize_filename(title: str) -> str:
    """Remove or replace characters that are unsafe in file paths."""
    title = re.sub(r'[<>:"/\\|?*]', "_", title)
    title = title.strip(". ")
    return title[:120] or "video"


def _build_stem(title: str, page: int) -> str:
    """Build the default output stem for a video/page pair."""
    stem = _sanitize_filename(title)
    if page > 1:
        stem = f"{stem}_p{page}"
    return stem


def _is_directory_target(path: str) -> bool:
    """Best-effort detection for directory-style output arguments."""
    if os.path.isdir(path):
        return True
    if path.endswith(os.sep):
        return True
    return os.path.splitext(os.path.basename(path))[1] == ""


def _resolve_output_path(output: str | None, default_filename: str) -> str:
    """Resolve a final output path from either a directory or file target."""
    if output is None:
        return os.path.join(os.getcwd(), default_filename)

    expanded = os.path.abspath(os.path.expanduser(output))
    if _is_directory_target(expanded):
        os.makedirs(expanded, exist_ok=True)
        return os.path.join(expanded, default_filename)

    parent = os.path.dirname(expanded)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return expanded


@click.command()
@click.argument("bv_or_url")
@click.option("--output", "-o", default=None, type=click.Path(), help="输出文件或目录。")
@click.option("--page", default=1, type=click.IntRange(1, None), help="分P 页码（默认 1）。")
@click.option("--container", default="mkv", type=click.Choice(["mkv", "mp4"]), help="DASH 合并容器（默认 mkv）。")
@click.option("--keep-raw", is_flag=True, help="保留 DASH 原始音视频流文件。")
def download(bv_or_url: str, output: str | None, page: int, container: str, keep_raw: bool):
    """下载视频文件，DASH 流会以无转码方式封装输出。"""
    from .. import client

    bvid = common.extract_bvid_or_exit(bv_or_url)
    cred = common.get_credential(mode="optional")

    info = common.run_or_exit(client.get_video_info(bvid, credential=cred), "获取视频信息")
    title = info.get("title", bvid)
    duration = info.get("duration", 0)
    stem = _build_stem(title, page)

    common.console.print(f"[bold]🎬 {title}[/bold]  ({common.format_duration(duration)})")
    common.console.print("[dim]获取下载流地址...[/dim]")
    streams = common.run_or_exit(
        client.get_video_download_streams(bvid, page=page, credential=cred),
        "获取下载流",
    )

    if streams["kind"] == "progressive":
        default_name = f"{stem}{streams.get('ext', '.mp4')}"
    else:
        default_name = f"{stem}.{container}"
    final_path = _resolve_output_path(output, default_name)

    if streams["kind"] == "progressive":
        part_path = final_path + ".part"
        try:
            common.console.print("[dim]下载视频中...[/dim]")
            common.run_or_exit(client.download_stream(streams["url"], part_path), "下载视频")
            os.replace(part_path, final_path)
        finally:
            if os.path.exists(part_path):
                os.unlink(part_path)

        size_mb = os.path.getsize(final_path) / (1024 * 1024)
        common.console.print(f"[green]✅ 视频已保存: {final_path} ({size_mb:.1f} MB)[/green]")
        return

    temp_dir = tempfile.mkdtemp(prefix="bili-download-", dir=os.path.dirname(final_path) or ".")
    video_raw = os.path.join(temp_dir, f"_video{streams.get('video_ext', '.m4s')}")
    audio_raw = os.path.join(temp_dir, f"_audio{streams.get('audio_ext', '.m4s')}")

    try:
        common.console.print("[dim]下载视频流中...[/dim]")
        common.run_or_exit(client.download_stream(streams["video_url"], video_raw), "下载视频流")

        common.console.print("[dim]下载音频流中...[/dim]")
        common.run_or_exit(client.download_stream(streams["audio_url"], audio_raw), "下载音频流")

        common.console.print("[dim]合并视频中...[/dim]")
        try:
            client.merge_streams_ffmpeg(video_raw, audio_raw, final_path)
        except Exception as e:
            common.exit_error(f"合并视频失败: {e}")
    finally:
        if not keep_raw:
            shutil.rmtree(temp_dir, ignore_errors=True)

    size_mb = os.path.getsize(final_path) / (1024 * 1024)
    common.console.print(f"[green]✅ 视频已保存: {final_path} ({size_mb:.1f} MB)[/green]")
