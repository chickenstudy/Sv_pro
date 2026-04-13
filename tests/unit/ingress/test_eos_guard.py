# Tests cho src/ingress/go2rtc_sync.py
#
# eos_guard.py đã bị xóa (Sprint 5) — go2rtc tự xử lý RTSP reconnect.
# Các test cũ cho EosStormGuard không còn áp dụng.
#
# TODO: Viết test mới cho go2rtc_sync.py:
#   - test fetch_cameras() với mock psycopg2
#   - test sync_to_go2rtc() với mock httpx
#   - test wait_for_go2rtc() retry logic
