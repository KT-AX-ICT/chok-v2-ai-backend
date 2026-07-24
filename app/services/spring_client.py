"""Spring 게이트웨이 내부 API 클라이언트.

신규 구조(7/10, api-spec §5.1): 분석 완료 시 **번들 + 분석결과를 한 번의 POST**로
저장 위임. (기존 번들/리포트 분리 저장·PATCH 방식 폐기.)

엔드포인트: POST /api/internal/reports
Spring 계약은 **camelCase**로 통일 — 페이로드 전체를 by_alias=True로 직렬화.
페이로드(api-spec §5.1, docs/spring-contract.md):
  - 번들 필드: /ingest 계약(camelCase) — bundleVersion·companyCode·window·triggerInfo·3종
  - status:  "DONE"(성공) / "FAILED"(에이전트 분석 실패)
  - severity: RcaResult.severity (HIGH/MID/LOW, NULL 허용)
  - result:  type·service + detail 5키(rca·summary·evidence·impact·actions), camelCase

계약 반영:
  - type·service는 result 내부에 포함 (최상위 아님 — Q-007 경로 확정)
  - logs/metrics/traces는 전송 직전 두 단계를 거침:
      (1) signal_selector — 모달리티별 상한 이내로 선별(원본 전량은 Spring DB 한계 초과)
      (2) raw_normalizer  — raw를 모달리티별 JSON으로 정규화
    Spring이 DB 저장 후 조회 시 역직렬화해 evidence 배열을 채움.
  - 모든 시각 필드는 Spring이 받는 형식(오프셋 포함 ISO)으로 맞춰 보냄 — _to_spring_ts
"""

from __future__ import annotations

import logging
from datetime import timezone

import httpx

from app.core.config import settings
from app.schemas.contracts import IngestBundle, RcaResult
from app.services.bundle_compression import parse_ts
from app.services.raw_normalizer import normalize_payload_signals
from app.services.signal_selector import PAYLOAD_KEYS, Selection, select_signals

logger = logging.getLogger(__name__)


def _to_spring_ts(value: str) -> str:
    """시각을 Spring이 받는 형식(오프셋 포함 ISO)으로 맞춘다 — `2026-07-24T02:33:23Z`.

    Spring이 허용하는 형식은 두 가지다.
      - 오프셋 포함 ISO : 2026-07-24T02:33:23Z / 2026-07-24T11:33:23+09:00
      - 공백형          : 2026-07-24 02:33:23 / 2026-07-24 02:33:23.209
    SDK가 보내는 값은 `2026-07-24T02:33:23.209880`(T 구분자·오프셋 없음)이라 둘 중
    어느 쪽도 아니다. 형식 위반은 Spring에서 422이므로 전송 직전에 맞춰준다.

    tz 없는 값은 UTC로 간주한다. SDK는 컨테이너 로그 시각을 변환 없이 그대로 싣고
    컨테이너가 UTC로 도는 것을 실측 확인했다(번들 윈도 끝 04:43:45 = 수집 시각 13:43 KST).
    이미 오프셋이 있는 값은 UTC로 환산하므로 시각 자체는 바뀌지 않는다.

    파싱 불가한 값은 여기 도달할 수 없다 — 시각 필드는 /ingest에서 한 번, 꺼낼 때
    restore_bundle의 model_validate에서 한 번, Iso8601로 두 번 검증되고 parse_ts는
    Iso8601을 전부 파싱한다. 이중 검증을 뚫고 온다면 코드 가정이 깨진 것이므로
    조용히 흘리지 않고 ValueError로 터뜨린다.
    """
    dt = parse_ts(value)
    if dt is None:
        raise ValueError(f"시각 파싱 실패 — 검증을 우회한 값: {value!r}")
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    text = dt.isoformat(timespec="microseconds" if dt.microsecond else "seconds")
    return text.replace("+00:00", "Z")


class SpringClient:
    def __init__(self) -> None:
        self._base = settings.spring_base_url.rstrip("/")

    # ---------------------------------------------------------- 페이로드 조립

    @staticmethod
    def _apply_selection(
        payload: dict, trigger_time: str, job_id: int | None
    ) -> dict[str, Selection]:
        """3종 배열을 상한 이내로 선별(in-place)하고 모달리티별 결과를 돌려준다."""
        selections: dict[str, Selection] = {}
        for key, modality in PAYLOAD_KEYS.items():
            selection = select_signals(modality, payload.get(key) or [], trigger_time)
            payload[key] = selection.items
            selections[modality] = selection
        dropped = {m: s.total for m, s in selections.items() if s.truncated}
        if dropped:
            logger.info(
                "job %s Spring 전송 선별: %s",
                job_id,
                ", ".join(
                    f"{m}={len(selections[m].items)}/{total}" for m, total in dropped.items()
                ),
            )
        return selections

    @staticmethod
    def _normalize_timestamps(payload: dict) -> None:
        """페이로드의 모든 시각 필드를 Spring 형식으로 맞춘다(in-place).

        선별 뒤에 부르므로 남은 항목만 손댄다. 대상은 window·triggerTime·3종 배열의
        timestamp, 그리고 modalityInfo 구간의 start/end까지 — 한 페이로드 안에서
        형식이 갈리면 받는 쪽이 파서를 두 벌 두어야 한다.
        """
        window = payload.get("window")
        if isinstance(window, dict):
            for key in ("start", "end"):
                if isinstance(window.get(key), str):
                    window[key] = _to_spring_ts(window[key])

        trigger = payload.get("triggerInfo")
        if isinstance(trigger, dict) and isinstance(trigger.get("triggerTime"), str):
            trigger["triggerTime"] = _to_spring_ts(trigger["triggerTime"])

        for array_key in PAYLOAD_KEYS:
            for item in payload.get(array_key) or []:
                if isinstance(item.get("timestamp"), str):
                    item["timestamp"] = _to_spring_ts(item["timestamp"])

        modality_info = payload.get("modalityInfo")
        if isinstance(modality_info, dict):
            for detail in modality_info.values():
                for interval in (detail or {}).get("intervals") or []:
                    for key in ("start", "end"):
                        if isinstance(interval.get(key), str):
                            interval[key] = _to_spring_ts(interval[key])

    @staticmethod
    def _annotate_sources(payload: dict, selections: dict[str, Selection]) -> None:
        """evidence.<모달리티>.source 끝에 수록 범위를 덧붙인다.

        선별은 LLM 실행 이후 단계라 LLM은 몇 건이 실릴지 알 수 없다. 그래서 코드가
        실제 선별 결과로 문구를 쓴다(항상 정확). conclusion은 건드리지 않는다 —
        LLM의 결론과 코드가 쓴 사실을 한 문장에 섞지 않기 위함.
        """
        evidence = payload.get("result", {}).get("evidence")
        if not isinstance(evidence, dict):
            return
        for modality, selection in selections.items():
            node = evidence.get(modality)
            if not isinstance(node, dict) or selection.total == 0:
                continue  # 항목이 없으면 표기할 건수도 없음
            note = (
                f"전체 {selection.total}건 중 주요 {len(selection.items)}건 수록"
                if selection.truncated
                else f"전체 {selection.total}건 전량 수록"
            )
            source = node.get("source")
            node["source"] = f"{source} ({note})" if source else note

    @staticmethod
    def _result_payload(
        bundle: IngestBundle, result: RcaResult, job_id: int | None = None
    ) -> dict:
        """분석 완료 페이로드. type·service는 result 내부에 두고, 3종은 선별 후 정규화."""
        detail = result.detail.model_dump(by_alias=True, exclude_none=True)
        payload = {
            **bundle.model_dump(by_alias=True),
            "status": "DONE",
            "severity": result.severity,
            "result": {"type": result.type, "service": result.service, **detail},
        }
        selections = SpringClient._apply_selection(
            payload, bundle.trigger_info.trigger_time, job_id
        )
        SpringClient._annotate_sources(payload, selections)
        SpringClient._normalize_timestamps(payload)
        normalize_payload_signals(payload)
        return payload

    @staticmethod
    def _failure_payload(
        bundle: IngestBundle, reason: str, job_id: int | None = None
    ) -> dict:
        """실패 페이로드. result 없이 status=FAILED + reason(계약: error 아님).

        result가 없어 고지할 자리가 없지만, 크기 위험은 같으므로 선별은 동일 적용한다.
        """
        payload = {
            **bundle.model_dump(by_alias=True),
            "status": "FAILED",
            "reason": reason,
        }
        SpringClient._apply_selection(payload, bundle.trigger_info.trigger_time, job_id)
        SpringClient._normalize_timestamps(payload)
        normalize_payload_signals(payload)
        return payload

    # ---------------------------------------------------------- 전송

    async def _post(self, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{self._base}/api/internal/reports", json=payload)
            if resp.is_error:  # 4xx/5xx — 상태코드·본문을 남겨 원인 추적 가능하게
                logger.warning(
                    "Spring 응답 오류 %s: %s", resp.status_code, resp.text[:500]
                )
            resp.raise_for_status()

    async def save_result(
        self, job_id: int, bundle: IngestBundle, result: RcaResult
    ) -> None:
        """분석 완료된 번들 + 분석결과를 한 번에 저장 위임 (api-spec §5.1)."""
        await self._post(self._result_payload(bundle, result, job_id))

    async def save_failure(
        self, job_id: int, bundle: IngestBundle, error: str
    ) -> None:
        """RCA 최종 실패(1회 재시도 후) 폴백 — 번들 + 실패 사유를 Spring에 전송.

        검증 통과 산출물이 없으므로 result는 보내지 않고, status=FAILED와 reason만 싣는다.
        """
        await self._post(self._failure_payload(bundle, error, job_id))


spring_client = SpringClient()
