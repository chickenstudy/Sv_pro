# SV-PRO Runbooks theo service (SLO / failure modes / recovery)

Tai lieu nay tom tat "di sau tung service" theo huong van hanh on dinh. Nguon chi tiet: [`docs/Runbook_Reliability_Restart.md`](../Runbook_Reliability_Restart.md) va [`docs/Reliability_Backpressure_ZMQ_EOS_Deadlock.md`](../Reliability_Backpressure_ZMQ_EOS_Deadlock.md).

## `video-ingress`

- **SLO**: FPS decode on dinh, khong spam EOS/reconnect.
- **Failure modes**:
  - RTSP disconnect → EOS storm.
  - Push nhanh hon AI core → backlog.
- **Recovery**:
  - Restart ingress instance truoc (co lap).
  - Neu lap lai: kiem tra RTSP/network, ap EOS guard.

## `ingress-manager`

- **SLO**: Camera active count khop voi DB, khong co process zombie.
- **Failure modes**:
  - RTSP camera ngat dot ngot → EOS storm → ai-core seq_id discrepancy.
  - Subprocess crash loop khong restart duoc.
- **EOS Guard Integration**:
  - Tu dong detect EOS storm (>5 EOS/giay).
  - Suppress EOS forwarded, restart subprocess.
  - Reset guard state khi reconnect thanh cong.
- **Recovery**:
  - Monitor logs: `EOS Storm DETECTED` → automatic restart.
  - Kiem tra metrics: `svpro_rtsp_disconnect_total`, `svpro_eos_storm_detected_total`.
  - Neu lap lai: kiem tra RTSP stream health, network latency.

## `savant-ai-core`

- **SLO**: `rate(svpro_frames_processed_total)` on dinh; latency `svpro_aicore_inference_ms` khong tang lien tuc.
- **Failure modes**:
  - Deadlock/treo pipeline (dac biet Ubuntu khi host lock/suspend).
  - Spike CUDA warmup (da co warmup trong LPR/FR).
  - SeqId discrepancy tu EOS storm → message loss detected.
- **Recovery**:
  - Ubuntu: xac nhan persistence mode.
  - Restart ai-core (sau khi chac ingress khong EOS storm).
  - Neu seq_id discrepancy lap lai: restart ingress-manager truoc.

## `json-egress`

- **SLO**: `./output` co file moi deu, khong disk-full.
- **Failure modes**:
  - Disk full / permission.
  - Subscriber lag.
- **Recovery**:
  - Giai phong disk, restart json-egress.

## `backend`

- **SLO**: `/health` = 200, `/metrics` scrape OK.
- **Failure modes**:
  - DB/Redis down → degraded.
- **Recovery**:
  - Restart backend sau khi DB/Redis healthy.

## `postgres` / `redis`

- **SLO**: exporters up; connection count/memory khong tang bat thuong.
- **Recovery**: theo thu tu: fix disk/IO → restart db/cache → restart backend.

## Monitoring (`prometheus` / `grafana`)

- **SLO**: scrape OK, dashboard cap nhat.
- **Failure modes**:
  - Prometheus khong scrape duoc `/metrics`.
- **Recovery**: kiem tra targets/up, network, ports.

## EOS Storm Detection & Recovery

### Symptoms

```
SeqId discrepancy is a symptom of message loss or stream termination without EOS
Frame XXXXX from source cam_online_1 is not a keyframe, skipping it.
EOS Storm DETECTED [cam_online_1]: X.X EOS/s > threshold=5
```

### Detection Metrics

| Metric | Alert threshold | Description |
|--------|-----------------|-------------|
| `svpro_eos_storm_detected_total` | >0 in 5min | EOS storm detected |
| `svpro_rtsp_disconnect_total` | >3 in 10min | RTSP disconnect events |
| `svpro_aicore_queue_depth` | >80 (80% of HWM) | ZMQ queue backlog |

### Recovery Steps

1. **Immediate**: Xem log `ingress-manager` — xem co automatic restart khong.
2. **If continues**: `docker compose restart ingress-manager`
3. **If still continues**: Kiem tra RTSP camera health tu NVR/camera vendor console.
4. **Last resort**: `docker compose restart savant-ai-core` (reset seq_id state)
