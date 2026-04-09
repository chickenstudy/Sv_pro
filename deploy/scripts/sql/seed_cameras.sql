-- Seed: Camera RTSP thật
INSERT INTO cameras (name, rtsp_url, location, zone, ai_mode, fps_limit, enabled)
VALUES (
    'cam_online_1',
    'rtsp://admin:abcd1234@192.168.42.140:554/snl/live/1/1',
    'Cổng chính',
    'gate',
    'both',
    15,
    true
)
ON CONFLICT DO NOTHING;
