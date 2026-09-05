"""Tests for audio extraction command and client functions."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from bili_cli import client
from bili_cli.cli import cli
from bili_cli.exceptions import AuthenticationError, BiliError, NetworkError


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_video_info():
    return {
        "title": "Test Video",
        "duration": 120,
        "stat": {"view": 1000},
        "owner": {"name": "TestUP", "mid": 123},
    }


# ===== Client tests =====


@pytest.mark.asyncio
async def test_get_audio_url_dash():
    mock_download_data = {"dash": {"audio": [{"baseUrl": "https://example.com/audio.m4s"}]}}
    mock_stream = MagicMock()
    mock_stream.url = "https://example.com/audio.m4s"
    mock_stream.audio_quality = 30216

    with patch("bili_cli.client.video.Video") as MockVideo, \
         patch("bili_cli.client.video.VideoDownloadURLDataDetecter") as MockDetector:
        MockVideo.return_value.get_download_url = AsyncMock(return_value=mock_download_data)
        detector_instance = MockDetector.return_value
        detector_instance.check_flv_mp4_stream.return_value = False
        detector_instance.detect_best_streams.return_value = [None, mock_stream]

        url = await client.get_audio_url("BV1test12345")
        assert url == "https://example.com/audio.m4s"


@pytest.mark.asyncio
async def test_get_audio_url_no_stream_raises():
    mock_download_data = {}

    with patch("bili_cli.client.video.Video") as MockVideo, \
         patch("bili_cli.client.video.VideoDownloadURLDataDetecter") as MockDetector:
        MockVideo.return_value.get_download_url = AsyncMock(return_value=mock_download_data)
        detector_instance = MockDetector.return_value
        detector_instance.check_flv_mp4_stream.return_value = False
        detector_instance.detect_best_streams.return_value = [None, None]

        with pytest.raises(BiliError, match="无法获取音频流"):
            await client.get_audio_url("BV1test12345")


@pytest.mark.asyncio
async def test_get_video_download_streams_dash():
    mock_download_data = {
        "dash": {
            "video": [
                {"id": 16, "bandwidth": 100, "baseUrl": "https://example.com/360p.m4s"},
                {"id": 32, "bandwidth": 200, "baseUrl": "https://example.com/video.m4s"},
            ],
            "audio": [
                {"id": 30216, "bandwidth": 64, "baseUrl": "https://example.com/audio-64.m4s"},
                {"id": 30280, "bandwidth": 128, "baseUrl": "https://example.com/audio.m4s"},
            ],
        }
    }

    with patch("bili_cli.client.video.Video") as MockVideo, \
         patch("bili_cli.client.video.VideoDownloadURLDataDetecter") as MockDetector:
        MockVideo.return_value.get_download_url = AsyncMock(return_value=mock_download_data)

        result = await client.get_video_download_streams("BV1test12345", page=2)

    assert result["kind"] == "dash"
    assert result["video_url"] == "https://example.com/video.m4s"
    assert result["audio_url"] == "https://example.com/audio.m4s"
    MockVideo.return_value.get_download_url.assert_awaited_once_with(page_index=1)
    MockDetector.assert_not_called()


@pytest.mark.asyncio
async def test_get_video_download_streams_dash_uses_raw_payload_when_detector_fails():
    mock_download_data = {
        "dash": {
            "video": [
                {"id": 16, "bandwidth": 100, "baseUrl": "https://example.com/360p.m4s"},
                {"id": 32, "bandwidth": 200, "baseUrl": "https://example.com/480p.m4s"},
            ],
            "audio": [
                {"id": 30216, "bandwidth": 64, "baseUrl": "https://example.com/audio-64.m4s"},
                {"id": 30280, "bandwidth": 128, "baseUrl": "https://example.com/audio-128.m4s"},
            ],
        }
    }

    with patch("bili_cli.client.video.Video") as MockVideo, \
         patch("bili_cli.client.video.VideoDownloadURLDataDetecter") as MockDetector:
        MockVideo.return_value.get_download_url = AsyncMock(return_value=mock_download_data)
        MockDetector.return_value.detect_best_streams.side_effect = AttributeError("'NoneType' object has no attribute 'value'")

        result = await client.get_video_download_streams("BV1test12345")

    assert result["kind"] == "dash"
    assert result["video_url"] == "https://example.com/480p.m4s"
    assert result["audio_url"] == "https://example.com/audio-128.m4s"


@pytest.mark.asyncio
async def test_get_video_download_streams_progressive():
    mock_download_data = {"durl": [{"url": "https://example.com/full.mp4"}]}
    mock_stream = MagicMock()
    mock_stream.url = "https://example.com/full.mp4"

    with patch("bili_cli.client.video.Video") as MockVideo, \
         patch("bili_cli.client.video.VideoDownloadURLDataDetecter") as MockDetector:
        MockVideo.return_value.get_download_url = AsyncMock(return_value=mock_download_data)
        detector_instance = MockDetector.return_value
        detector_instance.check_flv_mp4_stream.return_value = True
        detector_instance.detect_best_streams.return_value = [mock_stream]

        result = await client.get_video_download_streams("BV1test12345")

    assert result["kind"] == "progressive"
    assert result["url"] == "https://example.com/full.mp4"
    assert result["ext"] == ".mp4"


@pytest.mark.asyncio
async def test_get_video_download_streams_requires_both_dash_streams():
    with patch("bili_cli.client.video.Video") as MockVideo, \
         patch("bili_cli.client.video.VideoDownloadURLDataDetecter") as MockDetector:
        MockVideo.return_value.get_download_url = AsyncMock(return_value={"dash": {}})
        detector_instance = MockDetector.return_value
        detector_instance.check_flv_mp4_stream.return_value = False
        detector_instance.detect_best_streams.return_value = [MagicMock(url="https://example.com/video.m4s"), None]

        with pytest.raises(BiliError, match="无法获取完整视频流"):
            await client.get_video_download_streams("BV1test12345")


@pytest.mark.asyncio
async def test_get_episode_download_uses_one_authenticated_playurl_request():
    credential = MagicMock()
    download_data = {
        "play_view_business_info": {
            "season_info": {"title": "孤独摇滚！"},
            "episode_info": {"title": "3", "long_title": "馳せサンズ"},
        },
        "video_info": {
            "timelength": 1420000,
            "dash": {
                "video": [{"id": 80, "bandwidth": 1000, "baseUrl": "https://example.com/video.m4s"}],
                "audio": [{"id": 30280, "bandwidth": 192, "baseUrl": "https://example.com/audio.m4s"}],
            },
        },
    }

    with patch("bili_cli.client.bangumi.Episode") as MockEpisode:
        MockEpisode.return_value.get_download_url = AsyncMock(return_value=download_data)
        info, streams = await client.get_episode_download(693249, credential=credential)

    MockEpisode.assert_called_once_with(epid=693249, credential=credential)
    MockEpisode.return_value.get_download_url.assert_awaited_once_with()
    assert info == {
        "title": "孤独摇滚！ 第3集 馳せサンズ",
        "duration": 1420,
        "epid": 693249,
    }
    assert streams["kind"] == "dash"
    assert streams["video_url"] == "https://example.com/video.m4s"
    assert streams["audio_url"] == "https://example.com/audio.m4s"


@pytest.mark.asyncio
async def test_get_episode_download_requires_credential_before_request():
    with patch("bili_cli.client.bangumi.Episode") as MockEpisode:
        with pytest.raises(AuthenticationError, match="番剧下载需要登录"):
            await client.get_episode_download(693249, credential=None)
    MockEpisode.assert_not_called()


def test_split_audio_import_error():
    """split_audio should raise BiliError when PyAV is not installed."""
    import builtins

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "av":
            raise ImportError("no av")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(BiliError, match="PyAV"):
            client.split_audio("/nonexistent", "/tmp/test_out", segment_seconds=25)


def test_split_audio_invalid_segment_seconds():
    with patch.dict("sys.modules", {"av": MagicMock()}):
        with pytest.raises(BiliError, match="segment_seconds 必须大于 0"):
            client.split_audio("/nonexistent", "/tmp/test_out", segment_seconds=0)


@pytest.mark.asyncio
async def test_download_audio_streams_chunks_to_file():
    content = [b"abc", b"defgh", b""]

    class FakeContent:
        async def iter_chunked(self, _size):
            for c in content:
                yield c

    class FakeResponse:
        status = 200
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, _url, headers=None):
            return FakeResponse()

    with patch("bili_cli.client.aiohttp.ClientSession", FakeSession):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "a.m4a")
            n = await client.download_audio("https://example.com/audio.m4s", path)
            assert n == 8
            with open(path, "rb") as f:
                assert f.read() == b"abcdefgh"


@pytest.mark.asyncio
async def test_download_stream_writes_bytes():
    content = [b"abc", b"defgh", b""]

    class FakeContent:
        async def iter_chunked(self, _size):
            for c in content:
                yield c

    class FakeResponse:
        status = 200
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, _url, headers=None):
            return FakeResponse()

    with patch("bili_cli.client.aiohttp.ClientSession", FakeSession):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "stream.bin")
            n = await client.download_stream("https://example.com/video.m4s", path)
            assert n == 8
            with open(path, "rb") as f:
                assert f.read() == b"abcdefgh"


def test_merge_streams_ffmpeg_invokes_copy_mode():
    with patch("bili_cli.client.subprocess.run") as mock_run:
        client.merge_streams_ffmpeg("video.m4s", "audio.m4s", "out.mkv")

    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == ["ffmpeg", "-y", "-i", "video.m4s"]
    assert cmd[4:7] == ["-i", "audio.m4s", "-c"]
    assert cmd[7:] == ["copy", "out.mkv"]


def test_merge_streams_ffmpeg_missing_binary_raises():
    with patch("bili_cli.client.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(BiliError, match="ffmpeg"):
            client.merge_streams_ffmpeg("video.m4s", "audio.m4s", "out.mkv")


def test_merge_streams_ffmpeg_subprocess_failure_raises():
    import subprocess

    err = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="mux failed")
    with patch("bili_cli.client.subprocess.run", side_effect=err):
        with pytest.raises(BiliError, match="mux failed"):
            client.merge_streams_ffmpeg("video.m4s", "audio.m4s", "out.mkv")


# ===== CLI command tests =====


def test_audio_invalid_bvid(runner):
    result = runner.invoke(cli, ["audio", "invalid"])
    assert result.exit_code != 0


def test_audio_invalid_segment_range(runner):
    result = runner.invoke(cli, ["audio", "BV1test12345", "--segment", "2"])
    assert result.exit_code != 0


def test_audio_no_split_downloads_full(runner, mock_video_info):
    with patch("bili_cli.commands.common.get_credential", return_value=None), \
         patch("bili_cli.client.extract_bvid", return_value="BV1test12345"), \
         patch("bili_cli.client.get_video_info", new_callable=AsyncMock, return_value=mock_video_info), \
         patch("bili_cli.client.get_audio_url", new_callable=AsyncMock, return_value="https://example.com/audio.m4s"), \
         patch("bili_cli.client.download_audio", new_callable=AsyncMock, return_value=1024 * 1024) as mock_dl:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(cli, ["audio", "BV1test12345", "--no-split", "-o", tmpdir])
            assert result.exit_code == 0
            assert "音频已保存" in result.output
            mock_dl.assert_awaited_once()


def test_audio_split_mode(runner, mock_video_info):
    with tempfile.TemporaryDirectory() as tmpdir:
        seg_paths = [os.path.join(tmpdir, "seg_000.wav"), os.path.join(tmpdir, "seg_001.wav")]
        with patch("bili_cli.commands.common.get_credential", return_value=None), \
             patch("bili_cli.client.extract_bvid", return_value="BV1test12345"), \
             patch("bili_cli.client.get_video_info", new_callable=AsyncMock, return_value=mock_video_info), \
             patch("bili_cli.client.get_audio_url", new_callable=AsyncMock, return_value="https://example.com/audio.m4s"), \
             patch("bili_cli.client.download_audio", new_callable=AsyncMock, return_value=5 * 1024 * 1024), \
             patch("bili_cli.client.split_audio", return_value=seg_paths) as mock_split, \
             patch("bili_cli.commands.audio.os.path.getsize", return_value=960000), \
             patch("bili_cli.commands.audio.os.path.exists", return_value=True), \
             patch("bili_cli.commands.audio.os.unlink"):
            result = runner.invoke(cli, ["audio", "BV1test12345", "--segment", "25", "-o", tmpdir])
            assert result.exit_code == 0
            assert "切分完成: 2 段" in result.output
            mock_split.assert_called_once()


def test_audio_api_error_returns_nonzero(runner):
    with patch("bili_cli.commands.common.get_credential", return_value=None), \
         patch("bili_cli.client.extract_bvid", return_value="BV1test12345"), \
         patch("bili_cli.client.get_video_info", new_callable=AsyncMock, side_effect=Exception("api down")):
        result = runner.invoke(cli, ["audio", "BV1test12345"])
        assert result.exit_code != 0
        assert "获取视频信息" in result.output


def test_audio_download_error_returns_nonzero(runner, mock_video_info):
    with patch("bili_cli.commands.common.get_credential", return_value=None), \
         patch("bili_cli.client.extract_bvid", return_value="BV1test12345"), \
         patch("bili_cli.client.get_video_info", new_callable=AsyncMock, return_value=mock_video_info), \
         patch("bili_cli.client.get_audio_url", new_callable=AsyncMock, return_value="https://example.com/audio.m4s"), \
         patch("bili_cli.client.download_audio", new_callable=AsyncMock, side_effect=NetworkError("timeout")):
        result = runner.invoke(cli, ["audio", "BV1test12345", "--no-split"])
        assert result.exit_code != 0
        assert "下载音频" in result.output
