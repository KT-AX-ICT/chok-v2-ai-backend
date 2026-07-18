"""RCA 오케스트레이터 진입점 — LangGraph 기반 LLM 구현이 기본.

job_queue의 기본 runner가 여기의 `orchestrator.run(job_id, bundle)`을 호출한다
(RcaRunner 시그니처). 그래프 구조·노드 구현은 app/agents/graph.py 참조.

테스트·특수 구성은 LlmOrchestrator(planner=..., agents=..., report_agent=...)로
노드 에이전트를 주입해 교체한다. LLM 클라이언트는 호출 시점에 생성되므로
임포트·인스턴스화 자체는 네트워크·API 키 없이 무해하다.
"""

from app.agents.graph import LlmOrchestrator

# 앱 전역 오케스트레이터
orchestrator = LlmOrchestrator()
