"""Planner 协议、证据和失败边界的离线回归测试。"""
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import Field

from app.agent.planner.planner_graph import PlannerAgent
from app.agent.registry import build_default_agent_registry
from app.agent.supervisor.runtime import AgentRuntime
from app.schemas.agent_schema import ActionResult, SupervisorDecision
from app.schemas.trip_schema import Itinerary, TripRequest
from app.tools.registry import build_default_tool_registry


class StructuredPlannerModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)
    bound_names: set = Field(default_factory=set)

    def bind_tools(self, tools, **kwargs):
        self.bound_names.update(tool.name if hasattr(tool, 'name') else tool['function']['name'] for tool in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(json.loads(next(m.content for m in messages if isinstance(m, HumanMessage))))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def call(name, args, call_id):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': call_id, 'type': 'tool_call'}])


def draft(count=1):
    return {'summary': '行程草稿', 'days': [{'day_index': 1, 'items': [
        {'item_id': f'item-{i}', 'place_id': f'place-{i}', 'name': f'模型名称{i}',
         'start_time': f'{9 + 2*i:02d}:00', 'duration_minutes': 60,
         'travel_from_previous_minutes': 999, 'estimated_cost': 999}
        for i in range(count)
    ], 'total_cost': 999, 'walking_distance_km': 999}], 'total_cost': 999, 'currency': 'USD'}


def source(count=1):
    return {'run_id': 'planner-test', 'intent': 'create', 'user_message': '去长沙玩一天',
            'trip_request': TripRequest(destination='长沙', days=1, start_date='2026-10-01'),
            'attraction_result': ActionResult(data={'candidates': [
                {'place_id': f'place-{i}', 'name': f'真实地点{i}', 'category': '景点',
                 'address': f'长沙地址{i}', 'ticket_price': None}
                for i in range(count)]})}


def cost_args(count=1, amount=None):
    return {'items': [{'category': 'tickets', 'description': f'place-{i}', 'amount': amount}
                      for i in range(count)]}


def route_args(mode='walking'):
    return {'origin': {'place_id': 'place-0', 'name': '真实地点0'},
            'destination': {'place_id': 'place-1', 'name': '真实地点1'}, 'mode': mode}


def final(plan=None, routes=None, cost_id='cost-1'):
    return call('_PlannerResult', {'draft_itinerary': plan or draft(),
                'route_selections': routes or [], 'cost_call_id': cost_id}, 'final-1')


def selection(call_id='route-1'):
    return {'from_item_id': 'item-0', 'to_item_id': 'item-1', 'tool_call_id': call_id}


def model_for(plan=None):
    return StructuredPlannerModel(responses=[call('estimate_itinerary_cost', cost_args(), 'cost-1'), final(plan)])


async def run_plan(state=None, model=None, *, remaining=3, limit=3, amap=None):
    state = state if state is not None else source()
    model = model if model is not None else model_for()
    tools = build_default_tool_registry(amap_service=amap)
    agents = build_default_agent_registry(tool_registry=tools)
    original = deepcopy(state)
    with tools.scope('planner', agents, state, remaining=remaining) as ledger:
        result = await PlannerAgent(model, tools, max_tool_calls=limit).run(state, '按用户要求编排')
    assert state == original
    return result, ledger, model


async def test_create_binds_tools_and_preserves_unknown_prices():
    state = source()
    state['current_itinerary'] = Itinerary.model_validate(draft())
    result, ledger, model = await run_plan(state)
    assert result.status == 'completed', result.message
    item = result.data['draft_itinerary']['days'][0]['items'][0]
    assert item['name'] == '真实地点0' and item['estimated_cost'] is None
    assert result.data['cost_estimate']['within_budget'] is None
    assert result.data['draft_itinerary']['days'][0]['date'] == '2026-10-01'
    assert model.seen[0]['current_itinerary'] is None
    assert {'calculate_route', 'estimate_itinerary_cost'} <= model.bound_names
    assert ledger.calls == 1


async def test_revise_receives_current_plan_and_original_message():
    old = Itinerary.model_validate(draft())
    old.days[0].items[0].estimated_cost = None
    state = {'intent': 'revise', 'current_itinerary': old, 'user_message': '十一点出发'}
    plan = draft()
    plan['days'][0]['items'][0]['start_time'] = '11:00'
    result, _, model = await run_plan(state, model_for(plan))
    assert result.status == 'completed', result.message
    assert model.seen[0]['intent'] == 'revise'
    assert model.seen[0]['current_itinerary'] == old.model_dump(mode='json')
    assert model.seen[0]['user_message'] == '十一点出发'
    assert result.data['draft_itinerary']['days'][0]['items'][0]['start_time'] == '11:00:00'


@pytest.mark.parametrize('change,message', [
    ({'intent': None}, '意图'), ({'intent': 'revise'}, '已有行程'),
    ({'trip_request': {'days': 1}}, '目的地或天数'),
    ({'attraction_result': None}, '候选地点'),
    ({'attraction_result': ActionResult(status='failed')}, '搜索尚未成功'),
])
async def test_invalid_inputs_stop_before_model(change, message):
    result, ledger, model = await run_plan({**source(), **change})
    assert result.status == 'failed' and message in result.message
    assert not model.seen and ledger.calls == 0


@pytest.mark.parametrize('problem,message', [
    ('unknown', '之外的地点'), ('duplicate', '编号为空或重复'),
    ('days', '天数与'), ('index', '连续递增'), ('empty', '至少需要一个'),
])
async def test_invalid_final_draft_is_rejected(problem, message):
    plan = draft(2)
    if problem == 'unknown':
        plan['days'][0]['items'][0]['place_id'] = 'invented'
    elif problem == 'duplicate':
        plan['days'][0]['items'][1]['item_id'] = 'item-0'
    elif problem == 'days':
        plan['days'].append(deepcopy(plan['days'][0]))
    elif problem == 'index':
        plan['days'][0]['day_index'] = 2
    else:
        plan['days'][0]['items'] = []
    result, ledger, _ = await run_plan(source(2), StructuredPlannerModel(responses=[final(plan)]))
    assert result.status == 'failed' and message in result.message
    assert ledger.calls == 0 and 'draft_itinerary' not in result.data


@pytest.mark.parametrize('remaining,limit', [(0, 3), (1, 3), (3, 1)])
async def test_tool_limit_cannot_be_exceeded(remaining, limit):
    model = StructuredPlannerModel(responses=[call('estimate_itinerary_cost', cost_args(), 'c1'),
                                             call('estimate_itinerary_cost', cost_args(), 'c2')])
    result, ledger, _ = await run_plan(model=model, remaining=remaining, limit=limit)
    assert result.status == 'failed'
    assert ledger.calls <= min(remaining, limit)


@pytest.mark.parametrize('responses,message', [
    ([final()], '费用工具调用证据'),
    ([call('estimate_itinerary_cost', cost_args(amount=0), 'c1')], '未知价格'),
    ([call('calculate_route', {'origin': {'place_id': 'invented', 'name': 'x'},
                              'destination': {'name': 'y'}}, 'r1')], '地点编号'),
    ([AIMessage(content='普通文本')], '有效的工具调用'),
    ([call('save_version', {}, 's1')], '未开放'),
])
async def test_invalid_protocol_or_unproven_facts_fail(responses, message):
    result, ledger, _ = await run_plan(model=StructuredPlannerModel(responses=responses))
    assert result.status == 'failed' and message in result.message
    assert ledger.calls == 0


async def test_cost_evidence_must_match_final_selected_places():
    model = StructuredPlannerModel(responses=[call('estimate_itinerary_cost', cost_args(2), 'cost-1'), final()])
    result, _, _ = await run_plan(source(2), model)
    assert result.status == 'failed' and '费用工具结果与最终草稿不一致' in result.message


@pytest.mark.parametrize('duration,start,message', [(None, '11:00', '有效的距离和耗时'),
                                                  (90, '10:00', '时间冲突')])
async def test_route_evidence_and_model_schedule_are_checked(duration, start, message):
    plan = draft(2)
    plan['days'][0]['items'][1]['start_time'] = start
    amap = AsyncMock()
    amap.calculate_route_between.return_value = {'distance_km': 2, 'duration_minutes': duration}
    model = StructuredPlannerModel(responses=[call('calculate_route', route_args(), 'route-1'),
                                              final(plan, [selection()])])
    result, _, _ = await run_plan(source(2), model, amap=amap)
    assert result.status == 'failed' and message in result.message


async def test_missing_model_is_unimplemented():
    result = await PlannerAgent(None, build_default_tool_registry()).run(source(), '生成')
    assert result.status == 'unimplemented'


async def test_runtime_merges_output_but_blocks_unknown_cost_save():
    state = source()
    tools = build_default_tool_registry()
    agents = build_default_agent_registry(tool_registry=tools)
    agents._agents['planner'] = PlannerAgent(model_for(), tools)
    runtime = AgentRuntime(agent_registry=agents, tool_registry=tools)
    state = {**await runtime.initialize(state), 'attraction_result': state['attraction_result']}
    for action in ('call_agent', 'validate', 'save'):
        state['last_decision'] = SupervisorDecision(action=action, target_agent='planner' if action == 'call_agent' else None)
        state.update(await runtime.execute_update(state))
        state.update(runtime.merge_update(state))
        if action == 'call_agent':
            assert state['status'] == 'running'
            assert isinstance(state['draft_itinerary'], Itinerary)
            assert state['tool_call_count'] == 1
    assert state['status'] == 'failed' and state['saved_version_no'] is None


async def test_internal_submission_remains_available_after_public_budget_is_spent():
    """最后一次业务调用耗尽额度后，内部提交仍可完成且不增加调用账本。"""
    result, ledger, _ = await run_plan(remaining=1, limit=1)
    assert result.status == 'completed', result.message
    assert ledger.calls == 1
    assert [record['tool'] for record in ledger.traces] == ['estimate_itinerary_cost']


async def test_final_submission_cannot_share_a_round_with_a_business_tool():
    """防止图拆分后绕过最终提交与业务查询互斥的协议约束。"""
    combined = AIMessage(content='', tool_calls=[
        *call('estimate_itinerary_cost', cost_args(), 'cost-1').tool_calls,
        *final().tool_calls,
    ])
    result, ledger, _ = await run_plan(model=StructuredPlannerModel(responses=[combined]))
    assert result.status == 'failed' and '不能与新工具查询同时提交' in result.message
    assert ledger.calls == 0


async def test_duplicate_tool_call_id_is_rejected_across_model_rounds():
    """同一规划图内重复调用编号必须在第二次工具执行之前拒绝。"""
    model = StructuredPlannerModel(responses=[
        call('estimate_itinerary_cost', cost_args(), 'duplicate-id'),
        call('estimate_itinerary_cost', cost_args(), 'duplicate-id'),
    ])
    result, ledger, _ = await run_plan(model=model)
    assert result.status == 'failed' and '编号缺失或重复' in result.message
    assert ledger.calls == 1
