"""展示工具结果驱动的下一步选择。

离线：uv run pytest tests/unit/agents/test_planner_autonomy.py -q -s
真实模型：uv run pytest tests/unit/agents/test_planner_autonomy.py -m integration -q -s
真实模型测试使用项目模型配置；地图使用可控夹具，不调用真实高德。
"""
from datetime import datetime, timedelta
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.core.log import logger
from tests.unit.agents.test_planner import (
    StructuredPlannerModel, call, cost_args, draft, final, route_args, run_plan, selection, source,
)


class FeedbackPlannerModel(StructuredPlannerModel):
    """模拟模型协议：根据已收到的工具消息作选择，非真实 LLM 能力证明。"""

    cost_first: bool = False
    decisions: list[str] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(json.loads(next(m.content for m in messages if isinstance(m, HumanMessage))))
        feedback = [m for m in messages if isinstance(m, ToolMessage)]
        costs = [m for m in feedback if m.name == 'estimate_itinerary_cost']
        routes = [m for m in feedback if m.name == 'calculate_route']
        if self.cost_first and not costs:
            response = call('estimate_itinerary_cost', cost_args(2), 'cost-1')
        elif not routes:
            response = call('calculate_route', route_args('walking'), 'walk-1')
        elif len(routes) == 1 and (routes[-1].status == 'error' or
                                  json.loads(routes[-1].content).get('duration_minutes', 999) > 30):
            response = call('calculate_route', route_args('driving'), 'drive-1')
        elif not costs:
            response = call('estimate_itinerary_cost', cost_args(2), 'cost-1')
        else:
            chosen = routes[-1]
            minutes = json.loads(chosen.content)['duration_minutes']
            plan = draft(2)
            # 根据工具耗时修改草稿，而不是由 Planner 的 Python 代码改时间。
            plan['days'][0]['items'][1]['start_time'] = (
                datetime(2026, 10, 1, 10) + timedelta(minutes=minutes)).strftime('%H:%M')
            response = final(plan, [selection(chosen.tool_call_id)])
        self.decisions.append(response.tool_calls[0]['name'] + ':' + response.tool_calls[0]['args'].get('mode', ''))
        return ChatResult(generations=[ChatGeneration(message=response)])


def scenario(walking_minutes=90, fail_walking=False):
    state = source(2)
    state['user_message'] = (
        '一天游览候选中的两个地点，必须都保留，每个停留60分钟，第一站09:00开始。'
        '请先查步行路线；步行耗时不超过30分钟就采用步行，否则查驾车并采用驾车。'
        '第二站安排在第一站结束加实际交通耗时之后。计算两处门票已知费用小计。'
    )
    amap = AsyncMock()

    async def query(*, origin, destination, mode, city):
        if mode == 'walking' and fail_walking:
            raise RuntimeError('secret-provider-key')
        return {'distance_km': 5 if mode == 'walking' else 6,
                'duration_minutes': walking_minutes if mode == 'walking' else 12}

    amap.calculate_route_between.side_effect = query
    return state, amap


@pytest.mark.parametrize('walking_minutes,cost_first,fail_walking', [
    (15, False, False), (90, False, False), (90, True, False), (90, False, True),
])
async def test_feedback_changes_next_tool_and_final_plan(walking_minutes, cost_first, fail_walking):
    state, amap = scenario(walking_minutes, fail_walking)
    model = FeedbackPlannerModel(responses=[], cost_first=cost_first)
    logs = []
    sink = logger.add(logs.append, format='{extra[run_id]}|{message}', enqueue=False)
    try:
        result, ledger, _ = await run_plan(state, model, amap=amap, remaining=4, limit=4)
    finally:
        logger.remove(sink)
    assert result.status == 'completed', result.message
    changed = walking_minutes > 30 or fail_walking
    expected_modes = ['walking', 'driving'] if changed else ['walking']
    actual_modes = [r['arguments']['mode'] for r in ledger.traces if r['tool'] == 'calculate_route']
    assert actual_modes == expected_modes
    assert ledger.traces[0]['tool'] == ('estimate_itinerary_cost' if cost_first else 'calculate_route')
    item = result.data['draft_itinerary']['days'][0]['items'][1]
    assert item['start_time'] == ('10:12:00' if changed else '10:15:00')
    assert result.data['route_info'][0]['mode'] == ('driving' if changed else 'walking')
    joined = ''.join(logs)
    assert 'event=planner_model_completed' in joined
    assert 'event=tool_completed' in joined and 'event=planner_evidence_checked' in joined
    completed = [message.record['extra'] for message in logs
                 if message.record['extra'].get('event') == 'planner_completed']
    assert len(completed) == 1 and completed[0]['status'] == 'completed'
    assert completed[0]['duration_ms'] >= 0
    assert 'planner-test|' in joined and 'secret-provider-key' not in joined
    print('\n离线反馈模型决策:', ' → '.join(model.decisions))
    print('采用路线:', result.data['route_info'][0]['mode'], '第二站:', item['start_time'])


@pytest.mark.integration
@pytest.mark.parametrize('walking_minutes', [15, 90])
async def test_real_llm_changes_decision_from_tool_feedback(walking_minutes):
    """真实调用配置的 LLM；不同地图结果必须导致不同工具链和最终选择。"""
    from app.core.llm import get_llm

    state, amap = scenario(walking_minutes)
    state['run_id'] = f'planner-live-{walking_minutes}'
    # 限定网络超时与工具预算；不访问数据库，不写入行程。
    model = get_llm().model_copy(update={'timeout': 60, 'max_retries': 0})
    result, ledger, _ = await run_plan(state, model, amap=amap, remaining=5, limit=5)
    decisions = [{'tool': r['tool'], 'mode': r.get('arguments', {}).get('mode'),
                  'status': r['status']} for r in ledger.traces]
    print('\n真实模型调用轨迹:', json.dumps(decisions, ensure_ascii=False))
    print('执行状态:', result.status, result.message)
    assert result.status == 'completed', result.message
    modes = [r['arguments']['mode'] for r in ledger.traces if r['tool'] == 'calculate_route']
    assert modes[0] == 'walking'
    if walking_minutes > 30:
        assert 'driving' in modes[1:]
    else:
        assert 'driving' not in modes
    assert result.data['route_info'][0]['mode'] == ('driving' if walking_minutes > 30 else 'walking')
    assert len(result.data['draft_itinerary']['days'][0]['items']) == 2
    assert any(r['tool'] == 'estimate_itinerary_cost' and r['status'] == 'completed' for r in ledger.traces)
    print('最终路线:', result.data['route_info'][0]['mode'])
    print('第二站开始:', result.data['draft_itinerary']['days'][0]['items'][1]['start_time'])
