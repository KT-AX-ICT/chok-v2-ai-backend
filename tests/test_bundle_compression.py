"""번들 압축기(bundle_compression) 단위 테스트 — SDK 실데이터 패턴 기반."""

from app.schemas.contracts import ModalityItem
from app.services.bundle_compression import (
    compress_logs,
    compress_metrics,
    compress_traces,
)


def _item(ts: str, service: str, raw: str) -> ModalityItem:
    return ModalityItem(timestamp=ts, service=service, raw=raw)


# ---------------------------------------------------------------- log dedup


def test_log_dedup_collapses_repeated_errors():
    """실데이터 패턴: 동일 에러가 타임스탬프·req_id만 바뀌며 200회 반복 → 1줄."""
    items = [
        _item(
            f"2026-01-15T10:{i // 60:02d}:{i % 60:02d}Z",
            "user-service",
            f"<error>: (UserHandler.h:190:RegisterUserWithId) req_id={1000000 + i} "
            "Failed to insert user j1 to MongoDB: E11000 duplicate key",
        )
        for i in range(200)
    ]
    out = compress_logs(items)
    assert "200건 → 1패턴" in out
    assert "×200" in out
    assert "10:00:00~10:03:19" in out  # 최초~최후 절대시각 축약 유지
    assert "E11000 duplicate key" in out  # 원문 샘플 보존


def test_log_dedup_keeps_rare_line_and_sorts_errors_first():
    items = [
        _item("2026-01-15T10:00:01Z", "svc-a", "INFO GetUser completed req_id=111111"),
        _item("2026-01-15T10:00:02Z", "svc-a", "INFO GetUser completed req_id=222222"),
        _item("2026-01-15T10:01:30Z", "svc-b", "ERROR connection refused to media-service"),
    ]
    out = compress_logs(items)
    lines = out.splitlines()
    assert "3건 → 2패턴" in lines[0]
    assert "ERROR" in lines[1]  # 에러 패턴 우선 정렬
    assert "connection refused" in out  # 희귀 라인 원문 유지
    assert "×2" in out


def test_log_empty():
    assert compress_logs([]) == "(없음)"


# ------------------------------------------------------------ metric 통계


def test_metric_detects_onset_and_peak():
    """실데이터 패턴: CPU 2%대 baseline → 트리거 후 80%대 급변."""
    baseline = [
        _item(f"2026-01-15T10:00:{i:02d}Z", "node", f"cpu_usage={2.0 + 0.1 * i}")
        for i in range(6)
    ]
    incident = [
        _item("2026-01-15T10:02:00Z", "node", "cpu_usage=53.5"),
        _item("2026-01-15T10:02:15Z", "node", "cpu_usage=86.8"),
        _item("2026-01-15T10:02:30Z", "node", "cpu_usage=80.4"),
    ]
    out = compress_metrics(baseline + incident, trigger_time="2026-01-15T10:01:30Z")
    assert "node\tcpu_usage" in out
    assert "onset=53.5@10:02:00" in out  # 최초 이탈점
    assert "peak=86.8@10:02:15" in out  # 최대 이탈점
    assert "base n=6" in out and "incid n=3" in out


def test_metric_unparsable_falls_back_to_raw():
    items = [_item("2026-01-15T10:00:00Z", "svc-a", "이상한 형식의 메트릭")]
    out = compress_metrics(items, trigger_time="2026-01-15T10:01:30Z")
    assert "미파싱 원문 통과" in out
    assert "이상한 형식의 메트릭" in out


def test_metric_empty():
    assert compress_metrics([], trigger_time="2026-01-15T10:01:30Z") == "(없음)"


# ------------------------------------------------------------- trace 집계


def test_trace_aggregates_and_keeps_exemplars():
    items = [
        _item(
            f"2026-01-15T10:01:{i:02d}Z",
            "compose-post",
            f'{{"operation": "upload_media", "duration_us": {(i + 1) * 1000}, "status": "OK"}}',
        )
        for i in range(10)
    ] + [
        _item(
            "2026-01-15T10:02:00Z",
            "compose-post",
            '{"operation": "upload_media", "duration_us": 16000000, "status": "TIMEOUT"}',
        )
    ]
    out = compress_traces(items)
    assert "compose-post\tupload_media\t×11\terr=1" in out
    assert "1.6e+04" in out  # max 지연(ms) 통계
    assert "exemplar 원문" in out
    assert "TIMEOUT" in out  # 에러 스팬 원문 보존
    assert "10:01=10 10:02=1" in out  # 분단위 볼륨 타임라인


def test_trace_regex_fallback_for_plain_text():
    items = [_item("2026-01-15T10:01:10Z", "media", "span 15000ms TIMEOUT")]
    out = compress_traces(items)
    assert "media\t?\t×1\terr=1" in out
    assert "span 15000ms TIMEOUT" in out  # 원문 exemplar


def test_trace_empty():
    assert compress_traces([]) == "(없음)"
