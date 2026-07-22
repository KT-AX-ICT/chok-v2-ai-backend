### POST /api/internal/reports — 번들 및 분석 저장

Request
```json
{
  "bundleVersion": "1.0",
  "companyCode": "SN001",          // 기업 식별 코드 (SDK 최상위). result와 별개로 그대로 전달, 미전송 시 기본값 SN001
  "window": {
    "start": "2026-01-15T10:00:00Z",
    "end": "2026-01-15T10:03:00Z"
  },
  "triggerInfo": {
    "triggerTime": "2026-01-15T10:01:30Z",
    "triggeredBy": [ "metric", "log" ]
  },
  // 아래 3종은 result와 별개로 log/metric/trace 테이블에 행 단위 저장 → 조회 시 evidence 배열의 출처
  // 항목 검증(위반 시 422): raw 필수(null 거부, "" 허용) · timestamp 필수(ISO-8601) · service 128자 이하(null이면 "")
  // raw는 문자열이되 내용은 모달리티별 JSON — 구조 필드는 raw 안, timestamp는 상위 필드로만(중복 금지)
  // FastAPI에서 정규화 진행(SDK 원본 → JSON 변환). 파싱 불가 필드는 키 유지 + 값 비움("")
  "logs":    [ { "timestamp": "...", "service": "...", "raw": "{\"level\":\"ERROR\",\"msg\":\"...\"}" } ],
  "metrics": [ { "timestamp": "...", "service": "...", "raw": "{\"label\":\"...\",\"value\":\"...\",\"threshold\":\"...\",\"exceeded\":true}" } ],
  "traces":  [ { "timestamp": "...", "service": "...", "raw": "{\"traceId\":\"...\",\"from\":\"...\",\"to\":\"...\",\"duration\":0,\"status\":\"...\"}" } ],
  
  "status": "DONE",
  "severity": "HIGH",              // HIGH / MID / LOW (MEDIUM 아님), NULL 허용
  "result": {
    "type": "",
    "service": "",
    // 아래는 화면 탭별 상세 내용
    // RCA 결론
    "rca": {
        "rootCause": "media-service 프로세스 강제 종료...",
        "propagation": "media-service → compose-post → nginx"
    },
    // 요약 탭
    "summary": {
        "highlight": "media-service가 00:01:57에 종료되어 104초간 로그 침묵...",  // 목록·상세 summary
        "chips": [],
        "errorTags": [],
        "neutralTags": []
    },
    // 원인 탭 — lines[]/spans[]/items[]는 전송 안 함. Spring이 GET 응답 때 DB raw에서 조립해 채움
    "evidence": {
        "log": {
            "source": "...",
            "conclusion": "..."
        },
        "trace": {
            "source": "...",
            "conclusion": "..."
        },
        "metric": {
            "source": "...",
            "conclusion": "..."
        }
    },
    // 영향 탭
    "impact": {
        "metrics": [
        {
            "label": "...",
            "value": "..."
        }
        ],
        "affected": [
        {
            "service": "compose-post-service",
            "errors": 12,
            "type": "..."
        }
        ]
    },
    // 조치 탭
    "actions": {
        "steps": [
        "media-service 재시작 정책 점검",
        "..."
        ],
        "recovery": "..."
    }
  },
  "reason": "..."
}
```


Response 201 
```json
{ "reportId": 1 }
```

### 계약 사항
- `status=FAILED`일 때 `reason` 필드에 실패사유 전달
- `status`는 `DONE` 또는 `FAILED`만 허용 — 그 외 값은 422. `DONE`이면 `result` 필수, `FAILED`면 생략 가능.
- 멱등키 = triggerInfo.triggerTime, UNIQUE. 같은 triggerTime 재전송은 409.
- `result`는 Spring이 검증하지 않는 JSON 패스스루 — 내부 구조 정합성은 AI 백엔드 책임.
- `window`는 optional(null 허용). `triggerInfo.triggerTime`은 필수(멱등키 원천).
- `companyCode`는 기업 식별 코드 — SDK 번들 최상위로 수신, FastAPI가 그대로 전달(패스스루). 미전송 시 기본값 `SN001`.
- 원본 3종 `raw`는 FastAPI가 모달리티별 JSON으로 정규화해 전송 — log `{level,msg}` · trace `{traceId,from,to,duration,status}` · metric `{label,value,threshold,exceeded}`.
    - 파싱 불가 필드는 키를 남기고 값만 비움(`""`). log 전체 실패 시 원문 한 줄을 `msg`에 통째로 넣음.
    - 핵심 필드(가능하면 반드시 채움): log=`msg`, trace=`status`, metric=`label`+`value`.
- `result.evidence.*`에는 `lines`/`spans`/`items`를 싣지 않음 — AI는 `source`+`conclusion`만 생성. Spring이 GET 응답 때 DB `raw`에서 조립해 채움.
- DB `raw` 컬럼은 원본 한 줄이 아니라 정규화된 JSON 문자열로 저장 — 원문 보존 안 함. 기존 D-021(raw 무파싱·verbatim)과 상충하므로 공유 스펙(원본)에서 갱신 필요.

### 수정 사항 (2026-07-21)

개정 내역 — 실제 Spring(main) 계약 대조 + 원본/증거 중복 제거 설계 반영:
- 응답 키 `report_id` → `reportId` 정정 (실제 컨트롤러와 일치)
- `severity` 값은 `HIGH`/`MID`/`LOW` 명시 (`MEDIUM` 아님), NULL 허용
- `status`는 `DONE`/`FAILED`만 허용 — `FAILED` 시 `reason`으로 사유 전달, `DONE` 시 `result` 필수
- 멱등키 = `triggerInfo.triggerTime`, 같은 값 재전송은 409
- 원본 3종 `raw`를 모달리티별 JSON 문자열로 정규화 — `timestamp`는 상위 필드로만 두어 중복 제거
- `raw` 정규화 담당 = FastAPI, 파싱 불가 필드는 키만 남기고 값 비움(`""`)
- `result.evidence.*`에서 `lines`/`spans`/`items` 제거 — AI는 `source`+`conclusion`만 생성
- DB `raw`는 정규화 JSON으로 저장, 원문 미보존

후속 작업 (담당별):
- ✅ [FastAPI] `raw` 정규화 단계 추가 — `raw_normalizer`가 모달리티별 JSON으로 변환, `spring_client` 전송 직전 적용
- ✅ [FastAPI] `result` payload에 `type`·`service` 포함 (result 내부)
- ✅ [프롬프트] report 프롬프트가 `type`·`service`를 추론·출력 (report.md에 이미 반영, 확인 완료)
- ✅ [프롬프트] 모달리티 프롬프트·evidence 모델 정비 — `evidence`는 `source`+`conclusion`만 (`lines`/`spans`/`items` 제거)
- [Spring] GET 상세 응답 때 `log`/`metric`/`trace` 테이블 `raw`를 역직렬화해 `evidence.*`의 `lines`/`spans`/`items` 조립 (현재는 `counts`만 반환, 행 조회 메서드 없음)
- [Spring] `companyCode` 저장·활용 여부 결정 — 리포트를 기업 단위로 연결할지 (현재 AI는 전달만)
- [공유 스펙] D-021(raw 무파싱·verbatim) 갱신 — raw = 정규화 JSON·원문 미보존으로 변경
