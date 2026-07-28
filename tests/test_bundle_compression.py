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


def test_log_dedup_generalizes_unseen_format():
    """사전에 하드코딩 안 된 시스템 로그 형식도 가변부(경로·PID·호스트)를 학습해 dedup."""
    items = [
        _item(
            f"2026-01-15T10:00:{i:02d}Z",
            "kernel",
            f"sshd[{4000 + i}]: Accepted publickey for deploy from 10.0.{i}.5 port {50000 + i}",
        )
        for i in range(30)
    ]
    out = compress_logs(items)
    # 30건이 소수 패턴으로 수렴 — 정규식 3종엔 없던 sshd/포트/호스트 형식
    assert "30건 → 1패턴" in out
    assert "×30" in out


def test_log_empty():
    assert compress_logs([]) == "(없음)"


def test_log_surprise_sorts_acute_above_chronic():
    """평소(트리거 이전)에 많던 만성 패턴은 뒤로, 트리거 이후에만 튄 급성 패턴은 앞으로.
    surprise = incid/(base+baseline+1) 기준."""
    items = (
        # 만성 ERROR: 트리거 전 5회 + 후 1회 → surprise 낮음
        [_item(f"2026-01-15T09:5{i}:00Z", "user", "ERROR dup key 111") for i in range(5)]
        + [_item("2026-01-15T10:02:00Z", "user", "ERROR dup key 111")]
        # 급성 INFO: 트리거 전 0 + 후 2회 → surprise 높음
        + [_item("2026-01-15T10:02:10Z", "media", "INFO Starting the media-service server"),
           _item("2026-01-15T10:02:11Z", "media", "INFO Starting the media-service server")]
    )
    out = compress_logs(items, trigger_time="2026-01-15T10:00:00Z")
    data = [ln for ln in out.splitlines() if not ln.startswith("#")]
    assert data[0].startswith("media")  # 급성이 만성보다 위


def test_log_base_incid_counts_in_output():
    """트리거 전/후 건수가 base=/incid=로 표기된다."""
    items = [
        _item("2026-01-15T09:59:00Z", "svc", "ERROR x"),   # 트리거 전
        _item("2026-01-15T10:01:00Z", "svc", "ERROR x"),   # 트리거 후
        _item("2026-01-15T10:02:00Z", "svc", "ERROR x"),
    ]
    out = compress_logs(items, trigger_time="2026-01-15T10:00:00Z")
    assert "base=1 incid=2" in out


def test_log_no_trigger_falls_back_to_level_count():
    """trigger_time 없으면 기존 레벨/count 정렬 유지(회귀 안전)."""
    items = [
        _item("2026-01-15T10:00:01Z", "a", "INFO ok"),
        _item("2026-01-15T10:00:02Z", "b", "ERROR boom"),
    ]
    out = compress_logs(items)  # 트리거 없음
    data = [ln for ln in out.splitlines() if not ln.startswith("#")]
    assert data[0].startswith("b")  # ERROR 먼저


def test_load_baseline_profile_absent_returns_empty(monkeypatch, tmp_path):
    """프로파일 파일이 없으면 조용히 빈 dict(운영·CI 기본 상태)."""
    import app.services.bundle_compression as bc

    bc._load_baseline_profile.cache_clear()
    monkeypatch.setattr(bc, "_BASELINE_PROFILE_PATH", tmp_path / "nope.json")
    assert bc._load_baseline_profile() == {}
    bc._load_baseline_profile.cache_clear()


# ------------------------------------------------ 진원 후보 하이라이트


def _highlight_part(out: str) -> str:
    """하이라이트 섹션(정상 dedup 목록 앞부분)만 잘라낸다."""
    return out.split("# 로그 패턴 dedup")[0]


def test_highlight_surfaces_service_restart_despite_low_surprise():
    """서비스 재시작(lifecycle) 로그는 surprise 낮아도 하이라이트에 노출."""
    items = (
        # media 재시작: base 1(초기 부팅) + incid 1(재시작) → surprise 낮음
        [_item("2026-01-15T09:58:00Z", "media", "INFO Starting the media-service server...")]
        + [_item("2026-01-15T10:00:30Z", "media", "INFO Starting the media-service server...")]
        # 시끄러운 급성 에러(하이라이트 아님)
        + [_item(f"2026-01-15T10:0{i}:10Z", "user", "ERROR dup key 111") for i in range(1, 6)]
    )
    out = compress_logs(items, trigger_time="2026-01-15T10:00:00Z", triggered_by=["log"])
    hl = _highlight_part(out)
    assert "진원 후보 하이라이트" in out
    assert "media" in hl and "Starting the media-service server" in hl


def test_highlight_trigger_time_log_when_log_triggered():
    """log 트리거면 트리거 시각 로그(연결 실패 등)를 하이라이트에 노출."""
    items = [
        _item("2026-01-15T10:00:00Z", "composepost", "ERROR Failed to connect media-service-client"),
        _item("2026-01-15T10:05:00Z", "other", "INFO fine far away"),
    ]
    out = compress_logs(items, trigger_time="2026-01-15T10:00:00Z", triggered_by=["log"])
    hl = _highlight_part(out)
    assert "트리거 시각" in hl
    assert "Failed to connect media-service-client" in hl


def test_highlight_skips_trigger_group_when_not_log_triggered():
    """metric만 트리거면 트리거 로그 그룹은 생략(로그창은 노이즈)."""
    items = [_item("2026-01-15T10:00:00Z", "svc", "INFO normal traffic at trigger")]
    out = compress_logs(items, trigger_time="2026-01-15T10:00:00Z", triggered_by=["metric"])
    assert "트리거 시각" not in out


def test_highlight_absent_when_no_signal():
    """lifecycle·트리거 신호 없으면 하이라이트 섹션 자체가 없다."""
    items = [_item("2026-01-15T10:05:00Z", "svc", "INFO ok far from trigger")]
    out = compress_logs(items, trigger_time="2026-01-15T10:00:00Z", triggered_by=["log"])
    assert "진원 후보 하이라이트" not in out


def test_highlight_absent_without_trigger_time():
    """trigger_time 없으면 하이라이트 없음(회귀 안전)."""
    items = [_item("2026-01-15T10:00:00Z", "media", "INFO Starting the media-service server...")]
    out = compress_logs(items)
    assert "진원 후보 하이라이트" not in out


# ------------------------------------------------------------ metric 통계


def test_metric_anomalous_series_sorted_first():
    """알파벳상 뒤에 있어도 이상점이 큰 시리즈가 먼저 나온다."""
    base = (
        [_item(f"2026-01-15T10:00:{i:02d}Z", "n", '{"aaa": 10.0}') for i in range(5)]
        + [_item(f"2026-01-15T10:00:{i:02d}Z", "n", '{"zzz": 1.0}') for i in range(5)]
    )
    incid = [
        _item("2026-01-15T10:02:00Z", "n", '{"aaa": 10.0}'),   # 이상 없음
        _item("2026-01-15T10:02:00Z", "n", '{"zzz": 99.0}'),   # 큰 이탈
    ]
    out = compress_metrics(base + incid, trigger_time="2026-01-15T10:01:30Z")
    data = [ln for ln in out.splitlines() if ln.startswith("n\t")]
    assert data[0].startswith("n\tzzz")  # 이상점 시리즈가 먼저


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


def test_metric_parses_flat_json():
    """평면 JSON 메트릭: {"cpu_usage": ...} — 통계 산출(원문 통과 아님)."""
    items = [
        _item(f"2026-01-15T10:00:{i:02d}Z", "node", f'{{"cpu_usage": {2.0 + 0.1 * i}}}')
        for i in range(6)
    ] + [_item("2026-01-15T10:02:00Z", "node", '{"cpu_usage": 86.8}')]
    out = compress_metrics(items, trigger_time="2026-01-15T10:01:30Z")
    assert "node\tcpu_usage" in out
    assert "peak=86.8@10:02:00" in out
    assert "미파싱 원문 통과" not in out  # JSON도 파싱됨


def test_metric_parses_name_value_json():
    """name·value 쌍 JSON: {"metric": "cpu", "value": ...} — 라벨은 metric 값."""
    items = [
        _item(f"2026-01-15T10:00:{i:02d}Z", "node", f'{{"metric": "cpu", "value": {2.0 + i}}}')
        for i in range(3)
    ]
    out = compress_metrics(items, trigger_time="2026-01-15T10:01:30Z")
    assert "node\tcpu" in out  # value 키가 아니라 metric 값이 라벨


def test_metric_parses_prometheus_exposition():
    """Prometheus 노출형: 'node_cpu{labels} value' — 라벨셋 드롭, 지표명으로 시리즈."""
    items = [
        _item(
            f"2026-01-15T10:00:{i:02d}Z",
            "node",
            f'node_cpu_seconds{{instance="node-exporter:9100"}} {2.0 + i}',
        )
        for i in range(3)
    ]
    out = compress_metrics(items, trigger_time="2026-01-15T10:01:30Z")
    assert "node\tnode_cpu_seconds" in out
    assert "미파싱 원문 통과" not in out


def test_metric_json_bool_is_not_a_metric():
    """bool 필드는 숫자로 오인하지 않는다 (isinstance(True, int) 함정)."""
    items = [_item("2026-01-15T10:00:00Z", "svc", '{"healthy": true}')]
    out = compress_metrics(items, trigger_time="2026-01-15T10:01:30Z")
    assert "미파싱 원문 통과" in out  # 숫자 없음 → 통과


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


def test_trace_extracts_otel_name_key():
    """OTel 스팬은 오퍼레이션을 name 키에 담는다 — operation 없이도 인식."""
    items = [
        _item(
            "2026-01-15T10:01:00Z",
            "compose-post",
            '{"name": "upload_media", "duration_ms": 12, "status": "OK"}',
        )
    ]
    out = compress_traces(items)
    assert "compose-post\tupload_media\t×1" in out  # name이 오퍼레이션으로 승격
    assert "\t?\t" not in out  # 미상(?)으로 강등되지 않음


def test_trace_regex_fallback_for_plain_text():
    items = [_item("2026-01-15T10:01:10Z", "media", "span 15000ms TIMEOUT")]
    out = compress_traces(items)
    assert "media\t?\t×1\terr=1" in out
    assert "span 15000ms TIMEOUT" in out  # 원문 exemplar


def test_trace_empty():
    assert compress_traces([]) == "(없음)"
