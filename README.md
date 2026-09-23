# trip_agents：Supervisor 自主决策框架

主入口：`app.agent.supervisor.supervisor_graph.build_autonomous_graph()`。
FastAPI 入口：`app.main:app`。阶段三统一验收与日志规范接入已完成；HTTP 业务接口仍为明确的 501 骨架。
路径迁移见 [阶段一交付说明](docs/REFACTOR_PHASE1.md)，职责拆分见 [阶段二交付说明](docs/REFACTOR_PHASE2.md)，最终验收见 [阶段三交付说明](docs/REFACTOR_PHASE3.md)。
Supervisor 每次输出一个经过 Pydantic 校验的 `SupervisorDecision`，执行后读取更新的状态再决策。
Graph 没有固定的“搜索 → 规划”业务边；规则 fallback 是离线决策策略，LLM 模式可以选择不同动作。

```text
START → load_context → supervisor
                         ├─ ask_user
                         ├─ call_agent
                         ├─ validate
                         ├─ save
                         ├─ finish
                         └─ fail
                              ↓
                         merge_result
                              ├─ running → supervisor
                              └─ completed / needs_input / failed → END
```

## 核心接口与数据

| 文件 | 职责 |
| --- | --- |
| `app/schemas/agent_schema.py` | `SupervisorDecision`、`ActionResult` 共享契约 |
| `app/schemas/trip_schema.py` | 内部旅行、行程、路线及校验结果；公开 DTO 独立保存在 `api_schema.py` |
| `app/schemas/requirement_schema.py` | 需求解析结果与多轮需求补丁 |
| `app/schemas/place_search_schema.py`、`planner_schema.py` | 搜索事实、筛选结果和模型最终提交协议 |
| `app/agent/supervisor/supervisor_state.py` | `AgentRunState`：业务数据、中间结果、计数和 trace |
| `app/agent/supervisor/context.py` | `AutonomousGraphContext` 注入 LLM、高德、TripService、Validator、注册表和 Supervisor |
| `app/agent/base.py` | `SpecialistAgent.run(state, instruction)` 与 `DecisionProvider.decide(state)` 协议 |
| `app/agent/requirement.py`、`stub.py` | 需求 Agent、离线需求规则和专业能力占位基类 |
| `app/agent/registry.py` | `AgentRegistry`：Agent 注册、发现和默认装配 |
| `app/tools/amap.py` | 应用级地图工具，校验城市和本轮 POI 证据，调用领域服务 |
| `app/client/amap_client.py` | httpx 请求、Key 注入、超时、限流、有限重试和错误脱敏 |
| `app/services/amap_service.py` | POI 去重、城市校验、坐标及详情/路线/天气转换 |
| `app/tools/cost.py`、`app/services/cost_service.py` | 工具适配可信预算；业务函数汇总费用并保留未知价格 |
| `app/tools/pending.py` | 尚未接入服务的工具定义，明确返回 unimplemented |
| `app/tools/registry.py` | `ToolRegistry`、`ToolSpec`：持有工具对象、维护权限元数据、按 Agent 提供工具 |
| `app/tools/execution.py` | `guarded_tool`：统一执行权限、预算、串行化、脱敏和审计 |
| `app/tools/context.py` | `ToolContext`：本次动作的身份、需求快照、服务依赖、剩余额度、账本和锁 |
| `app/schemas/tool_schema.py` | 工具输入校验模型；runtime 是框架注入参数，不向模型公开 |
| `app/agent/supervisor/decision.py`、`fallback.py` | 分别提供模型主管决策与生产规则回退 |
| `tests/fixtures/agents.py` | 测试专用的 `FakeSupervisorAgent` |
| `app/agent/supervisor/runtime.py` | `AgentRuntime`、`RuntimeLimits`，Graph 节点使用的决策、动作和合并规则 |
| `app/agent/supervisor/supervisor_graph.py` | LangGraph 条件边、独立动作节点和统一合并出口 |
| `app/agent/place_search/place_search_graph.py` | PlaceSearchAgent，通过 LangChain create_agent 编排搜索，保留城市约束和 POI 证据校验 |
| `app/prompts/` | 提示词字符串常量，不创建模板、模型或 Runnable |
| `app/services/trip_service.py` | Runtime 委托短会话加载；保存及公开查询入口仍明确报告未实现 |

决策 action 为 `ask_user / call_agent / validate / save / finish / fail`。
每个决策包含 `target_agent`、`instruction`、`reason`、`finish` 和可选 `metadata`。
仅 `call_agent` 接受 Agent 目标；工具的选择、参数和调用由专业 Agent 内部负责。
非法 action、未知名称、越权、参数或输出解析错误都经过明确的 fail 出口。
终止 action 自动规范化为 `finish=true`，非终止 action 不允许同时声明结束。

状态中不放客户端、数据库会话或 LLM 实例。
专业 Agent 收到独立状态快照，返回 `ActionResult(status, message, data)`；
可预期的失败返回 `status="failed"`，Runtime 统一保留失败数据和工具轨迹。
结果由 Runtime 暂存，再由 `merge_result` 合并，不能自由覆盖计数、权限或校验证明。
每条 trace 包含决策、原因、结果、错误和内部工具调用。解析或限额拒绝也有 trace。

## Agent 和工具边界

| Agent | 职责 | allowed_tools | 当前实现 |
| --- | --- | --- | --- |
| requirement | 解析需求和意图，不生成完整行程 | 无 | 复用 LLMService；无模型时有限规则解析 |
| attraction_search | 搜索筛选候选，不安排每日行程 | search_attractions、get_place_detail | create_agent 搜索、按需查看详情并筛选 |
| accommodation | 搜索住宿地点，不执行预订 | search_accommodation、get_place_detail、calculate_route | Agent 保持 unimplemented 桩 |
| planner | 组合或修改草稿，不编造路线和价格 | calculate_route、estimate_itinerary_cost | 按 intent 生成结构化草稿，查询相邻路线并汇总已知费用；未知费用保留警告 |
| place_knowledge | 带来源的景点问答，不修改计划 | 无 | unimplemented 桩 |
| weather_impact | 查询天气事实，不修改行程 | get_weather_forecast | Agent 保持 unimplemented 桩 |
| SupervisorAgent | 只决策下一步动作 | 无 | 不持有业务工具 |

工具只查询或确定性计算，不代替 Agent 做业务决策。名称、描述和输入 schema 由应用代码定义。
工具调用必须同时通过 Agent 的 `allowed_tools` 和 `ToolSpec.allowed_agents`。
所有调用通过 `ToolRegistry`、`ToolContext` 和 `guarded_tool`，检查权限与预算、记录 trace 并脱敏错误。
数据库写入和版本保存不能注册成普通工具。

景点搜索使用 LangChain `create_agent` 管理消息和循环，通过 `_SearchDecision` 提交筛选结果。
默认最多 3 次应用工具调用，实际额度取该上限与 Runtime 剩余预算的较小值。
搜索与详情共用额度，失败调用也计数；最终结构化提交不消耗工具额度。
`ModelCallLimitMiddleware` 最多允许“实际工具调用额度 + 1”次模型调用。
同批调用在当前上下文内串行执行；退出上下文后工具失效。
城市始终来自当前 `TripRequest.destination`，模型传入其他城市不会改变实际查询城市。
详情 ID 只接受当前 Agent 本次作用域中成功搜索到的 POI；不能跨 Agent 或跨运行复用证据。
高德未提供的门票、营业时间、室内属性保持 `null`，不由模型填写。

## 工具层分工与调用链

应用工具在模块加载时使用 `@tool` 和 `@guarded_tool` 定义，由注册表持有并复用。
`ToolSpec` 补充允许的 Agent、副作用、只读和实现状态；未实现工具不向模型开放。

```text
专业 Agent
  → ToolRegistry → ToolContext / guarded_tool
  → 应用级工具
  → AmapService（领域转换和校验）
  → AmapClient（HTTP 通信与通用响应校验）
  → 高德 Web API
```

`ToolRuntime.context` 由框架注入，服务、身份、预算、证据和账本不接受模型参数覆盖。
`calculate_route` 对 Agent 隐藏高德坐标格式；已有坐标直接使用，缺坐标时由服务内部地理编码。
Agent 不获得独立地理编码工具。

| 应用工具 | 高德 Web API | 事实边界 |
| --- | --- | --- |
| search_attractions | `/v3/place/text` | 返回去重 POI，不推断价格、营业时间、室内属性或推荐时长 |
| get_place_detail | `/v3/place/detail` | 仅查询本轮搜索证据中的 POI，未知字段为 null |
| calculate_route | `/v3/direction/walking`、`/v3/direction/driving`；必要时 `/v3/geocode/geo` | 返回距离、耗时、提供方、查询时间和警告 |
| estimate_itinerary_cost | 无，确定性 Python | 汇总已知交通/门票/餐饮/住宿，未知项单列 |
| search_accommodation | `/v3/place/text` | POI 只能证明地点存在；房价、库存、可预订状态未知 |
| get_weather_forecast | `/v3/weather/weatherInfo` | 有出发日期才查询，城市取旅行需求；不修改行程 |

六个应用工具均有基础实现。未注入高德服务或缺少天气出发日期时明确返回 `unimplemented`；
天气上游不可用时返回 `unavailable`。工具可用不代表住宿、天气等专业 Agent 已完整实现。

费用结果包含 `known_total`、分类 `breakdown`、`unknown_items`、预算和假设。
未知费用不作为 0 参与已知总额；存在未知项时 `within_budget=null`。
只有已确认免费的项目才使用价格 0。

新增工具时在 `app/tools/` 中定义函数并使用统一装饰器，注册 `ToolSpec`，
再给对应 Agent 授予权限；`get()` 返回工具对象，`get_spec()` 返回元数据。

Supervisor 只决策；`validate` 复用确定性 `ValidationService`。
`save` 要求本轮草稿、需求、路线通过校验；三者发生变化会使校验失效，
保存前还会再次运行确定性校验。同一份草稿在一次运行中不能重复保存。
Repository 返回有效旅行编号和版本号之后才标记成功。

## Supervisor 如何选择下一步

注入 `LLMService` 时，通过已有 Prompt + 模型 + PydanticOutputParser 生成决策；
Prompt 包含 Agent 边界、决策结构、完成条件和保存约束，主管不接收工具清单或工具参数 schema。
没有注入模型时使用规则 fallback，不会主动读取密钥或连接真实模型。
已配置模型发生异常时明确失败，不静默改成另一种策略。

规则策略先调用 requirement，再根据合并后的需求决定：

- create 缺目的地或天数则追问；信息完整后搜索，再交给 planner。
- direct_search 只要求目的地，候选搜索完成即可结束。
- revise 没有已有行程则追问，有行程则交给 planner。
- knowledge 交给 place_knowledge，不要求旅行天数。
- other/unsupported 或任务返回 unimplemented 则明确失败。

住宿、知识与天气 Agent 仍为桩；相关基础工具的实现不代表完整专业 Agent 已完成。
Planner 按 `state.intent` 新建或修改草稿。`create_agent` 直接绑定路线和费用工具，模型自主选择
工具、顺序和参数，并根据返回结果调整方案；Python 不预设调用顺序，也不替模型顺延时间。
默认最多 3 次工具调用（包含 1 次费用汇总），可配置更高上限，但仍受 Runtime 剩余总额度约束；
工具失败会交还模型，可在额度内换方式或重试；最终必须引用成功的工具调用证据。
代码验收最终地点、每段路线、时间冲突及费用明细的一致性。支持步行和驾车，
跨天交通、住宿及完整旅行费用尚未覆盖。
费用未知保持 `null`，汇总数值仅为已知项目小计；现有 Validator 会阻止未知项目费用的草稿保存。
完整规划质量及真实模型、地图和保存链路仍需集成验证。

Planner 日志复用 Loguru，由应用 lifespan 显式初始化后默认写入 `logs/app_YYYYMMDD.log`（可由 `LOG_*` 配置调整）。
按 `run_id` 记录模型轮次、工具选择、交通方式、路线耗时、费用小计、失败及最终验收状态；
不记录密钥、提示词、模型内部思考或上游异常正文。

自主调用演示测试（`-s` 显示决策轨迹）：

```powershell
# 离线模拟模型：验证反馈改变选择、不同调用顺序、工具失败后换方式。
uv run pytest tests/unit/agents/test_planner_autonomy.py -q -s
# 使用 .env 配置的真实模型：地图使用固定夹具，不调用真实高德或数据库。
uv run pytest tests/unit/agents/test_planner_autonomy.py -m integration -q -s
```

真实模型测试比较两种反馈：步行 15 分钟时保留步行，步行 90 分钟时再查并采用驾车。
只有真实模型测试通过，才说明本次配置的模型完成了这两个受控场景；离线测试仅验证 Agent 执行机制。

## Runtime 与 LangGraph

唯一执行入口为 `build_autonomous_graph()` 构建的 LangGraph。
Graph 节点依次调用 `initialize → decide_update → execute_update → merge_update`，
由 LangGraph 应用状态更新、追加轨迹、选择条件边并管理 checkpointer。
`AgentRuntime` 只提供这些节点所需的权限、异常、保存和预算规则。

`RuntimeLimits` 默认最多 12 次决策、8 次 Agent 执行、8 次工具执行；
失败的实际调用也计入预算。Graph 的调度步数上限按业务迭代次数设置，避免先撞上默认 recursion limit。
Runtime 采用失败即退出，`retry_count` 预留为 0；HTTP 客户端另有有限重试。
AmapClient 默认每秒最多 3 个请求、超时 10 秒、最多重试 2 次。
限流按客户端实例生效，不是跨进程共享配额。应用工具调用和内部 HTTP 请求分别计量：
一次路线工具调用可能包含必要的地理编码和路线请求，内部请求同样经过客户端限流。

每次新用户消息生成新 run_id，清空上一轮结果、计数、追问和校验状态，保留已知需求。
无 checkpointer 时把上轮返回状态和新的 user_message 一起传入；
有 checkpointer 时使用相同 thread_id。提供 trip_id 时必须注入 TripService，由服务使用短会话加载当前版本。

## 离线运行与接入

在项目根目录：

```powershell
uv run pytest tests/unit/agents/test_autonomous_framework.py -q
uv run pytest -q
```

离线测试使用 FakeSupervisor、脚本化模型及地图夹具验证图与工具行为。
当前没有独立 demo 模块，不使用不存在的模块作为运行入口。

离线完整“预置行程 → 校验 → 假 TripService 保存 → 完成”测试：

```powershell
uv run pytest -q tests/unit/agents/test_autonomous_framework.py -k fake_flow
```

真实服务由调用方注入，不由 State 或 Agent 创建。执行 `uv sync --locked`，
在 `.env` 配置高德 Web 服务类型的 `AMAP_API_KEY`：

```python
from app.agent.supervisor.supervisor_graph import build_autonomous_graph
from app.agent.supervisor.context import AutonomousGraphContext
from app.client.amap_client import AmapClient
from app.services.amap_service import AmapService
from app.core.config import get_settings

async def search_trip(llm_service):
    async with AmapClient(get_settings().amap_api_key) as client:
        return await build_autonomous_graph().ainvoke(
            {"user_message": "找长沙的博物馆"},
            context=AutonomousGraphContext(llm=llm_service, amap=AmapService(client)),
        )
```

客户端负责 Key 注入，错误信息不回显密钥、请求 URL 或上游原始错误正文。
默认 pytest 使用 `httpx.MockTransport`、FakeAmapClient/FakeAmapService 等离线夹具，
不验证真实高德联通性、模型决策质量或数据库事务。

## 迁移与限制

- 高德接入使用直接 HTTP；专业 Agent 只看到六个应用级工具名称。
- 搜索事实模型保留高德 POI 身份、城市、坐标和来源；规划候选模型保留规划用途字段。
  事实转换不能自动填充游玩时长、票价或室内属性。
- 当前已接入 Planner 草稿生成；住宿 Agent、景点知识 RAG、天气重规划和自动预订支付仍未实现。
- `TripService.save_version()` 与 `TripRepository.save_version()` 明确抛出 `CapabilityUnavailableError`，不执行真实写入；Runtime 已通过 TripService 接入，保存仍未实现。
- 规则需求解析仅适合明确中文城市与 1~14 天天数；真实自然语言解析应注入 LLM。
- 状态和 trace 用于单次进程或 LangGraph checkpoint，未实现跨进程调度、事务幂等或补偿。
  注册表是应用权限边界，不是执行不受信任 Python 的安全沙箱。


## 当前 HTTP 与资源边界

- 注册、登录已移除内存实现，替换为异步 Router → AuthService（延迟数据库会话工厂）与 UserRepository（AsyncSession）骨架。注册、登录、令牌校验及 SQL 读写暂未实现；合法注册登录请求返回 **501 / CAPABILITY_UNAVAILABLE**。
- 会话、运行、事件、旅行、版本共 10 项操作已注册到 OpenAPI。合法请求返回 **501 / CAPABILITY_UNAVAILABLE**，不创建任务、不查询业务数据。正式 Bearer 鉴权与归属校验尚未接入这些占位路由。
- SSE 路由在建流前返回普通 JSON 501，不发送成功事件。包括认证在内的业务入口统一使用 ErrorResponse，携带 `X-Request-Id`；auth_schema 复用正式 API 契约，拒绝额外字段并保留密码空白。
- `core/resources.py` 延迟创建数据库、模型和地图资源，`app.main` 生命周期释放自有连接；外部注入资源仍由调用方关闭。数据库工作通过 TripService 创建短会话，模型推理期间不持有会话。
- 直接在脚本中创建 `AppResources` 时，调用方应在 `finally` 中 `await resources.aclose()`；`get_llm()` 是独立模型工厂，不再有进程全局客户端缓存。
- 日志配置由入口管理；独立 Graph 脚本需要自行调用 `configure_logger()` / `shutdown_logger()`。文件日志按当前单应用、单进程配置使用，多进程日志聚合不在本阶段实现。

阶段三仅做离线结构及行为验收；真实模型、地图、数据库和保存事务尚未验证。


## 日志与 PyCharm 运行

日志规范见本机 `知识点了解/Python应用编码规范.md` 第 16.2 节。项目沿用 Loguru，应用 lifespan 显式初始化及关闭，导入模块不创建日志文件。默认目录 `logs` 位于项目根目录，文件为 `app_YYYYMMDD.log`；通过 `.env` 的 `LOG_ENABLED`、`LOG_CONSOLE_ENABLED`、`LOG_FILE_ENABLED`、`LOG_LEVEL`、`LOG_ROTATION`、`LOG_RETENTION` 调整。

日志用 `event` 标识稳定事件，HTTP 使用 `request_id`，Agent 使用 `run_id`，耗时统一为 `duration_ms`。记录请求方法、路由模板、状态码，以及动作、目标 Agent、轮次、工具结果与调用次数。外部服务只记录操作、尝试次数和耗时；请求体、认证头、完整查询字符串、提示词、模型推理、工具原始参数/结果及异常原文不进入新增事件日志。占位认证和未实现保存均不会记录成功。

HTTP 摘要统计响应头就绪时的耗时，不代表 SSE 流结束时间。技术日志不替代持久审计。默认 INFO；节点进入/退出仅 DEBUG。底层模型 SDK 重试次数与 token 用量不可见时不虚构；高德客户端记录实际尝试次数。

PyCharm 配置：

1. 解释器选择项目 `.venv/Scripts/python.exe`，工作目录选择项目根目录。
2. Python 运行配置选择 **模块名 `uvicorn`**，参数为 `app.main:app --host 127.0.0.1 --port 8000`；不使用已删除的根目录 main.py。
3. 默认测试运行器为 pytest，目标目录 `tests`；默认排除 integration 标记，不调用真实外部服务。
4. 单独创建模块运行配置 `scripts.verify_refactor`，执行导入、依赖与 DDL 的只读离线验收；不会建表。

```powershell
uv run python -m scripts.verify_refactor
uv run pytest -q
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Uvicorn CLI 的监听配置以命令参数为准，`.env` 中的 `APP_HOST/APP_PORT` 不会自动覆盖 CLI。HTTP 骨架（含认证）可导入启动，真实模型/地图/数据库在首次使用时才需要对应配置。
