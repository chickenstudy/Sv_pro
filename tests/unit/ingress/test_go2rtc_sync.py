"""
Tests cho src/ingress/go2rtc_sync.py.

Thay thế test_eos_guard.py (eos_guard.py đã bị xóa ở Sprint 5).
"""

from unittest.mock import MagicMock, patch, call
import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_camera_rows():
    """Tạo danh sách CameraRow giả cho tests."""
    from src.ingress.go2rtc_sync import CameraRow
    return [
        CameraRow(id=1, name="cam_01", rtsp_url="rtsp://192.168.1.10:554/stream", source_id="cam_01", enabled=True),
        CameraRow(id=2, name="cam_02", rtsp_url="rtsp://192.168.1.11:554/stream", source_id="cam_02", enabled=True),
        CameraRow(id=3, name="cam_03", rtsp_url="rtsp://192.168.1.12:554/stream", source_id="cam_03", enabled=False),
    ]


# ── fetch_cameras() ────────────────────────────────────────────────────────────

class TestFetchCameras:

    def test_returns_camera_rows_on_success(self):
        """DB trả về rows → fetch_cameras parse thành danh sách CameraRow."""
        from src.ingress.go2rtc_sync import fetch_cameras, CameraRow

        fake_rows = [
            (1, "cam_01", "rtsp://192.168.1.10/stream", "cam_01", True),
            (2, "cam_02", "rtsp://192.168.1.11/stream", "cam_02", True),
        ]
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = fake_rows
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("psycopg2.connect", return_value=mock_conn):
            result = fetch_cameras("postgresql://test")

        assert len(result) == 2
        assert isinstance(result[0], CameraRow)
        assert result[0].source_id == "cam_01"
        assert result[0].enabled is True
        assert result[1].rtsp_url == "rtsp://192.168.1.11/stream"

    def test_returns_empty_list_on_db_error(self):
        """DB lỗi → fetch_cameras trả về [] (không crash)."""
        from src.ingress.go2rtc_sync import fetch_cameras

        with patch("psycopg2.connect", side_effect=Exception("connection refused")):
            result = fetch_cameras("postgresql://bad-dsn")

        assert result == []

    def test_executes_correct_sql(self):
        """fetch_cameras gọi SELECT đúng câu query."""
        from src.ingress.go2rtc_sync import fetch_cameras

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("psycopg2.connect", return_value=mock_conn):
            fetch_cameras("postgresql://test")

        execute_call = mock_cur.execute.call_args[0][0]
        assert "cameras" in execute_call.lower()
        assert "enabled" in execute_call.lower()

    def test_closes_connection_on_success(self):
        """Connection được đóng sau khi fetch xong."""
        from src.ingress.go2rtc_sync import fetch_cameras

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("psycopg2.connect", return_value=mock_conn):
            fetch_cameras("postgresql://test")

        mock_conn.close.assert_called_once()


# ── sync_to_go2rtc() ───────────────────────────────────────────────────────────

class TestSyncToGo2rtc:

    def test_adds_enabled_cameras(self):
        """Camera enabled → PUT /api/streams được gọi."""
        from src.ingress.go2rtc_sync import sync_to_go2rtc, CameraRow

        cameras = [CameraRow(1, "cam_01", "rtsp://cam01/stream", "cam_01", True)]

        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=200, json=lambda: {})
        mock_put_resp = MagicMock()
        mock_put_resp.raise_for_status = MagicMock()
        mock_client.put.return_value = mock_put_resp

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"):
            sync_to_go2rtc(cameras, mock_client)

        mock_client.put.assert_called_once()
        put_kwargs = mock_client.put.call_args
        assert put_kwargs[1]["params"]["name"] == "cam_01"
        assert put_kwargs[1]["content"] == "rtsp://cam01/stream"

    def test_skips_disabled_cameras(self):
        """Camera disabled → không PUT lên go2rtc."""
        from src.ingress.go2rtc_sync import sync_to_go2rtc, CameraRow

        cameras = [CameraRow(1, "cam_off", "rtsp://cam/stream", "cam_off", False)]

        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=200, json=lambda: {})

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"):
            sync_to_go2rtc(cameras, mock_client)

        mock_client.put.assert_not_called()

    def test_removes_stale_streams(self):
        """Stream tồn tại trên go2rtc nhưng không còn trong DB → DELETE."""
        from src.ingress.go2rtc_sync import sync_to_go2rtc, CameraRow

        cameras = [CameraRow(1, "cam_01", "rtsp://cam01/stream", "cam_01", True)]
        existing_streams = {"cam_01": {}, "cam_stale": {}}  # cam_stale không còn trong DB

        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=200, json=lambda: existing_streams)
        mock_client.put.return_value = MagicMock(raise_for_status=MagicMock())
        mock_client.delete.return_value = MagicMock(raise_for_status=MagicMock())

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"):
            sync_to_go2rtc(cameras, mock_client)

        mock_client.delete.assert_called_once()
        delete_kwargs = mock_client.delete.call_args
        assert delete_kwargs[1]["params"]["name"] == "cam_stale"

    def test_handles_go2rtc_api_error_gracefully(self):
        """go2rtc API không phản hồi → sync_to_go2rtc không crash, log error."""
        from src.ingress.go2rtc_sync import sync_to_go2rtc, CameraRow

        cameras = [CameraRow(1, "cam_01", "rtsp://cam/stream", "cam_01", True)]

        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("connection refused")

        # Không raise exception
        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"):
            sync_to_go2rtc(cameras, mock_client)

        mock_client.put.assert_not_called()

    def test_skips_camera_with_empty_rtsp_url(self):
        """Camera enabled nhưng rtsp_url rỗng → bỏ qua."""
        from src.ingress.go2rtc_sync import sync_to_go2rtc, CameraRow

        cameras = [CameraRow(1, "cam_01", "", "cam_01", True)]

        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=200, json=lambda: {})

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"):
            sync_to_go2rtc(cameras, mock_client)

        mock_client.put.assert_not_called()


# ── wait_for_go2rtc() ──────────────────────────────────────────────────────────

class TestWaitForGo2rtc:

    def test_returns_true_when_ready_on_first_try(self):
        """go2rtc trả 200 ngay lần đầu → True."""
        from src.ingress.go2rtc_sync import wait_for_go2rtc

        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=200)

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"), \
             patch("time.sleep"):
            result = wait_for_go2rtc(mock_client, max_retries=5)

        assert result is True
        assert mock_client.get.call_count == 1

    def test_retries_on_connection_error(self):
        """Exception trên 2 lần đầu, lần 3 thành công → retry rồi trả True."""
        from src.ingress.go2rtc_sync import wait_for_go2rtc

        mock_client = MagicMock()
        mock_client.get.side_effect = [
            Exception("connection refused"),
            Exception("connection refused"),
            MagicMock(status_code=200),
        ]

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"), \
             patch("time.sleep"):
            result = wait_for_go2rtc(mock_client, max_retries=5)

        assert result is True
        assert mock_client.get.call_count == 3

    def test_returns_false_after_max_retries(self):
        """go2rtc không bao giờ trả lời → False sau max_retries."""
        from src.ingress.go2rtc_sync import wait_for_go2rtc

        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("timeout")

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"), \
             patch("time.sleep"):
            result = wait_for_go2rtc(mock_client, max_retries=3)

        assert result is False
        assert mock_client.get.call_count == 3

    def test_accepts_non_500_status_as_ready(self):
        """Status 404 (go2rtc up nhưng empty) → vẫn coi là ready."""
        from src.ingress.go2rtc_sync import wait_for_go2rtc

        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=404)

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"), \
             patch("time.sleep"):
            result = wait_for_go2rtc(mock_client, max_retries=3)

        assert result is True

    def test_rejects_500_status(self):
        """Status 500 → không coi là ready, tiếp tục retry."""
        from src.ingress.go2rtc_sync import wait_for_go2rtc

        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(status_code=500)

        with patch("src.ingress.go2rtc_sync.GO2RTC_URL", "http://svpro-go2rtc:1984"), \
             patch("time.sleep"):
            result = wait_for_go2rtc(mock_client, max_retries=3)

        assert result is False
        assert mock_client.get.call_count == 3
