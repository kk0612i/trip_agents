"""公开契约边界测试，不依赖数据库、模型或地图服务。"""

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.api_schema import (
    AuthResponse, Credentials, ItineraryDTO, ItineraryVersion, Page, PageQuery,
    RunResult, RunSubmission, RunView, SearchCandidateDTO, SearchResultDTO,
    SessionCreate, SessionSummary, SSEEvent, TripRequestDTO, ValidationDTO,
)

RUN = "550e8400-e29b-41d4-a716-446655440001"
SESSION = "550e8400-e29b-41d4-a716-446655440000"
CLIENT = "550e8400-e29b-41d4-a716-446655440010"
NOW = "2026-09-22T10:00:00Z"


def run_data(status="queued"):
    data = dict(run_id=RUN, session_id=SESSION, client_request_id=CLIENT,
                message="长沙三天", status=status, created_at=NOW)
    if status != "queued":
        data["started_at"] = NOW
    if status in {"completed", "needs_input", "failed"}:
        data.update(finished_at=NOW, response="本轮结果")
    if status == "completed":
        data.update(intent="direct_search", result={"search": {}})
    if status == "needs_input":
        data.update(pending_question="几天？", missing_fields=["days"])
    if status == "failed":
        data.update(error={"code": "RUN_INTERRUPTED", "message": "运行中断", "retryable": True})
    return data


def itinerary_data():
    return {"summary": "一天行程", "days": [{"day_index": 1, "items": [{
        "item_id": "i1", "place_id": "p1", "name": "景点", "start_time": "09:00:00",
        "duration_minutes": 60,
    }]}]}


def version_data():
    return dict(trip_id="42", version_no=1, source_run_id=RUN,
                trip_request={"destination": "长沙", "days": 1}, itinerary=itinerary_data(),
                routes=[], validation={"passed": True, "checked_at": NOW}, created_at=NOW)


def test_submission_normalizes_and_hashes_only_logical_content():
    first = RunSubmission(client_request_id=CLIENT, message="  长沙三天  ")
    replay = RunSubmission(client_request_id=RUN, message="长沙三天", expected_version_no=None)
    assert first.message == "长沙三天"
    assert first.request_hash() == replay.request_hash()
    assert len(first.request_hash()) == 64
    assert first.request_hash() != RunSubmission(client_request_id=CLIENT, message="长沙三天", expected_version_no=1).request_hash()
    assert set(first.model_dump()) == {"client_request_id", "message", "expected_version_no"}
    assert len(RunSubmission(client_request_id=CLIENT, message="😀" * 4000).message) == 4000


@pytest.mark.parametrize("changes", [
    {"message": "  "}, {"message": "😀" * 4001}, {"message": 42},
    {"expected_version_no": True}, {"expected_version_no": "1"},
    {"expected_version_no": 0}, {"expected_version_no": 2**32},
    {"client_request_id": "not-a-uuid"}, {"client_request_id": 1},
    {"user_id": RUN}, {"steps": []},
])
def test_submission_rejects_invalid_or_internal_fields(changes):
    with pytest.raises(ValidationError):
        RunSubmission.model_validate({"client_request_id": CLIENT, "message": "长沙", **changes})


@pytest.mark.parametrize("value", [0, 42, "0", "-1", "01", "1.2", "18446744073709551616", True])
def test_trip_id_rejects_non_decimal_or_overflow(value):
    with pytest.raises(ValidationError):
        SessionCreate(trip_id=value)


def test_credentials_preserve_password_and_auth_is_strict():
    credentials = Credentials(email=" USER@Example.COM ", password="  secret  ")
    assert credentials.email == "user@example.com"
    assert credentials.password == "  secret  "
    response = AuthResponse(access_token="token", user={"id": RUN.replace("-", ""), "email": credentials.email})
    assert response.user.id == RUN
    assert response.token_type == "bearer"
    assert "password" not in response.model_dump_json()
    with pytest.raises(ValidationError):
        AuthResponse(token="demo-token", email=credentials.email)
    for invalid in ("not-an-email", "a" * 255 + "@example.com"):
        with pytest.raises(ValidationError):
            Credentials(email=invalid, password="password")


@pytest.mark.parametrize("value", [True, "12.34", -1, 1.001, float("inf"), float("nan"), 10**10, 10**400])
def test_money_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        TripRequestDTO(budget=value)


def test_money_and_date_serialization():
    request = TripRequestDTO(budget=9999999999.99, days=14, start_date="2026-09-22")
    assert request.model_dump(mode="json")["start_date"] == "2026-09-22"
    assert TripRequestDTO(budget=0).budget == 0
    assert TripRequestDTO().budget is None
    for changes in ({"days": 15}, {"days": True}, {"traveler_count": 0}, {"start_date": "2026-02-30"}):
        with pytest.raises(ValidationError):
            TripRequestDTO(**changes)


def test_page_and_utc_serialization():
    summary = SessionSummary(session_id=SESSION, title="新旅行", created_at="2026-09-22T18:00:00+08:00", updated_at=NOW)
    page = Page[SessionSummary](items=[summary])
    assert page.model_dump(mode="json")["items"][0]["created_at"] == NOW
    assert page.next_cursor is None
    assert PageQuery().limit == 20
    for value in (0, 101, True, "20"):
        with pytest.raises(ValidationError):
            PageQuery(limit=value)
    with pytest.raises(ValidationError):
        SessionSummary(session_id=SESSION, title="新旅行", created_at=datetime(2026, 9, 22), updated_at=NOW)


@pytest.mark.parametrize("status", ["queued", "running", "completed", "needs_input", "failed"])
def test_run_states_roundtrip(status):
    run = RunView.model_validate(run_data(status))
    assert RunView.model_validate_json(run.model_dump_json()) == run
    assert set(run.result.model_dump()) == {"trip_request", "search", "itinerary", "routes", "validation", "saved_version"}


@pytest.mark.parametrize("status,changes", [
    ("queued", {"response": "提前回答"}), ("queued", {"started_at": NOW}),
    ("running", {"started_at": None}), ("running", {"result": {"search": {}}}),
    ("completed", {"result": {}}), ("completed", {"intent": "create"}),
    ("completed", {"finished_at": None}), ("completed", {"pending_question": "问题"}),
    ("needs_input", {"pending_question": " "}), ("failed", {"error": None}),
    ("completed", {"finished_at": "2020-01-01T00:00:00Z"}),
])
def test_run_states_reject_inconsistent_results(status, changes):
    with pytest.raises(ValidationError):
        RunView.model_validate({**run_data(status), **changes})


def test_failure_before_start_and_after_save_are_representable():
    before_start = run_data("failed")
    before_start["started_at"] = None
    assert RunView.model_validate(before_start).started_at is None
    version = version_data()
    after_save = run_data("failed")
    after_save["result"] = {key: version[key] for key in ("trip_request", "itinerary", "routes", "validation")}
    after_save["result"]["saved_version"] = {"trip_id": "42", "version_no": 1}
    assert RunView.model_validate(after_save).result.saved_version.version_no == 1


def test_unknown_costs_stay_null_and_version_hash_ignores_metadata():
    itinerary = ItineraryDTO.model_validate(itinerary_data())
    assert itinerary.total_cost is None
    assert itinerary.model_dump(mode="json")["days"][0]["items"][0]["start_time"] == "09:00:00"
    version = ItineraryVersion.model_validate(version_data())
    changed_metadata = ItineraryVersion.model_validate({**version_data(), "version_no": 2, "source_run_id": CLIENT})
    assert version.content_hash() == changed_metadata.content_hash()
    explicit_defaults = version_data()
    explicit_defaults["itinerary"]["days"][0]["walking_distance_km"] = 0
    assert version.content_hash() == ItineraryVersion.model_validate(explicit_defaults).content_hash()
    changed_content = version_data()
    changed_content["itinerary"]["summary"] = "修改安排"
    assert version.content_hash() != ItineraryVersion.model_validate(changed_content).content_hash()
    for level in ("day", "trip"):
        data = itinerary_data()
        target = data["days"][0] if level == "day" else data
        target["total_cost"] = 0
        with pytest.raises(ValidationError):
            ItineraryDTO.model_validate(data)


def test_saved_version_requires_validated_snapshot_and_route_references():
    with pytest.raises(ValidationError):
        RunResult(saved_version={"trip_id": "42", "version_no": 1})
    data = version_data()
    data["routes"] = [{"from_item_id": "missing", "to_item_id": "i1", "distance_km": 1,
                       "duration_minutes": 10, "mode": "walking", "provider": "amap"}]
    with pytest.raises(ValidationError):
        ItineraryVersion.model_validate(data)
    with pytest.raises(ValidationError):
        ValidationDTO(passed=True, checked_at=NOW, issues=[{"severity": "error", "code": "invalid", "message": "错误"}])


def test_search_does_not_invent_facts_or_recommend_missing_places():
    candidate = dict(place_id="p1", name="博物馆", category="博物馆", city="长沙", longitude=112.9, latitude=28.2, source="amap")
    parsed = SearchCandidateDTO(**candidate)
    assert parsed.estimated_cost is None
    for changes in ({"longitude": 181}, {"latitude": float("nan")}, {"longitude": True},
                    {"estimated_cost": 0}, {"ticket_price": 10}, {"indoor": True}):
        with pytest.raises(ValidationError):
            SearchCandidateDTO(**{**candidate, **changes})
    with pytest.raises(ValidationError):
        SearchResultDTO(candidates=[parsed], recommendations=[{"place_id": "missing", "reason": "好看"}])


@pytest.mark.parametrize("status", ["completed", "needs_input", "failed"])
def test_sse_terminal_matches_run(status):
    envelope = dict(run_id=RUN, seq=3, occurred_at=NOW, data=run_data(status))
    event = SSEEvent(event=f"run.{status}", envelope=envelope)
    assert event.event_id == f"{RUN}:3"
    assert SSEEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError):
        SSEEvent(event="run.started", envelope=envelope)
    with pytest.raises(ValidationError):
        SSEEvent(event=f"run.{status}", envelope={**envelope, "run_id": CLIENT})


def test_sse_progress_and_start_and_invalid_sequence():
    envelope = dict(run_id=RUN, seq=1, occurred_at=NOW, data={"status": "running"})
    assert SSEEvent(event="run.started", envelope=envelope).envelope.data.status == "running"
    progress = {**envelope, "data": {"stage": "search", "message": "正在查找"}}
    assert SSEEvent(event="progress", envelope=progress).envelope.data.stage == "search"
    with pytest.raises(ValidationError):
        SSEEvent(event="progress", envelope=envelope)
    for seq in (0, True, "1"):
        with pytest.raises(ValidationError):
            SSEEvent(event="progress", envelope={**progress, "seq": seq})


def test_documented_json_examples_match_public_models():
    """文档中的可调用示例也走真实 DTO，避免类型与示例漂移。"""
    import re
    from pathlib import Path

    document = Path("docs/API_CONTRACT.md").read_text(encoding="utf-8")
    examples = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", document, re.S)]
    for example in examples:
        if "password" in example:
            Credentials.model_validate(example)
        elif "access_token" in example:
            AuthResponse.model_validate(example)
        elif "items" in example:
            Page[SessionSummary].model_validate(example)
        elif "result" in example:
            RunView.model_validate(example)
