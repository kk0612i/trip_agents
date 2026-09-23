# 重构阶段一：公共迁移底座

本阶段按已确认的重构方案迁移目录、公共 Schema、Prompt、测试夹具及入口，并建立旅行服务接口。
以迁移开始时的未提交工作区为基线，没有恢复 HEAD、清理既有改动、修改前端或升级依赖。
每个阶段交付后由用户审核，后续阶段独立推进。

## 当前完成范围

- FastAPI 导入入口统一为 `app.main:app`；删除项目根目录的 PyCharm 示例脚本。
- `app/agent` 采用单数命名，主管、Planner、景点搜索已有各自的子包。
- ORM 拆为基类、用户、会话、运行、旅行五个模块，运行事件与运行同组、版本与旅行同组。
- Pydantic 与 ORM 分离，公开 API DTO、现有认证模型和内部业务模型保持各自语义。
- Prompt 只保存字符串常量，原提示词正文不变；LLMService 不再为取提示词反向导入主管实现。
- `FakeSupervisorAgent` 及工具夹具只保留在 `tests/fixtures`；生产规则回退、需求解析与 Stub 仍在应用内。
- 数据库工厂、LLM 工厂、日志模块完成路径迁移；请求数据库依赖移到 API 层。
- 新增 `TripService`：每次加载通过工厂取得独立短会话，在返回行程模型前完成关闭。
- 新增 `CapabilityUnavailableError`；服务和 Repository 的保存占位明确抛出该异常，不以隐式 None 表示完成。

## 路径映射

表中旧路径用于迁移审阅，不能继续作为导入入口。

| 迁移前 | 迁移后 |
| --- | --- |
| `app/agents/autonomous_graph.py` | `app/agent/supervisor/supervisor_graph.py` |
| `app/agents/state.py` | `app/agent/supervisor/supervisor_state.py` |
| `app/agents/context.py` | `app/agent/supervisor/context.py` |
| `app/agents/runtime.py` | `app/agent/supervisor/runtime.py` |
| `app/agents/supervisor.py` | `app/agent/supervisor/decision.py`；其中 Fake 移入 `tests/fixtures/agents.py` |
| `app/agents/planner.py` | `app/agent/planner/planner_graph.py`；输出模型移入 `schemas/planner_schema.py` |
| `app/agents/place_search.py` | `app/agent/place_search/place_search_graph.py`；输出模型移入 `schemas/place_search_schema.py` |
| `app/agents/specialists.py` 的公共协议 | `app/agent/base.py`；需求解析和 Stub 暂留 `app/agent/specialists.py` |
| `app/agents/registry.py` 及其他专业 Agent 文件 | `app/agent` 下同名模块 |
| `app/agents/prompts.py` 与主管内嵌提示词 | `app/prompts/{requirement,supervisor,planner,place_search}_prompt.py` |
| `app/models/entities.py` | `app/models/{base,user,session,run,trip}.py` |
| `app/models/schemas.py` | `app/schemas/{trip,requirement,agent,place_search}_schema.py` |
| `app/models/api_schemas.py` | `app/schemas/api_schema.py`，整体迁移 |
| `app/tools/schemas.py` | `app/schemas/tool_schema.py` |
| 认证路由中的 Credentials、UserView、AuthResponse | `app/schemas/auth_schema.py`，保留原有校验 |
| `app/db/session.py` | `app/core/db.py`；`get_db` 单独移到 `app/api/deps.py` |
| `app/client/llm_client.py`、`app/core/logger.py` | `app/core/llm.py`、`app/core/log.py` |
| `app/api/main.py`、`app/api/dependencies.py` | `app/main.py`、`app/api/deps.py` |
| `app/api/{auth,sessions,runs,trips}/router.py` | `app/api/{auth,session,run,trip}_router.py` |
| `test/`、`test/tool_fixtures.py` | `tests/`、`tests/fixtures/tools.py` |
| `script/` | `scripts/`，包括 SQL 文件及导出入口 |

不提供旧路径转发模块。原路径下可能存在本机忽略的 Python 缓存；它们不属于交付源码。

## 公共契约与下一阶段接线

### 已冻结的 Agent 契约

- Graph 仍通过 `build_autonomous_graph()` 构建，参数和运行行为保持一致。
- `SpecialistAgent.run(state, instruction) -> ActionResult` 和 `DecisionProvider.decide(state) -> SupervisorDecision` 统一定义在 `agent/base.py`。
- 六个公共工具名、Agent 注册名、双向权限、工具预算和模型轮次不变。
- `_PlannerResult`、`_SearchDecision` 的真实类名、字段、默认值和描述不变；Planner 内部提交仍不计入公共工具额度。
- `AgentRunState` 字段、`steps` 追加 reducer、`Overwrite` 重置、多轮隔离及保存前校验规则不变。
- `ActionResult` 的 completed/failed/unimplemented/unavailable 保持原义；未知价格不能变成 0。

### TripService 接口

构造函数接收 `SessionFactory`，即每次产生独立 `AsyncSession` 上下文的可调用对象。
服务实例只保存工厂，不能保存某次请求的 Session。

```python
async def load_current(trip_id: int) -> tuple[int, Itinerary] | None: ...

async def save_version(
    trip_id: int | None,
    trip_request: TripRequest | None,
    change_request: TripChangeRequest | None,
    itinerary: Itinerary,
    routes: list[RouteInfo],
    validation: ValidationResult,
) -> tuple[int, int]: ...
```

`load_current` 在会话中调用原 Repository 查询，将 JSON 转为行程模型后关闭会话；
不存在返回 None，数据库和 Schema 错误继续传播。当前函数不承担用户归属鉴权。
`save_version` 尚不执行会话创建、事务、写入或幂等检查，直接抛出 `CapabilityUnavailableError`。

异常固定错误码为 `CAPABILITY_UNAVAILABLE`，`capability` 为可安全展示的能力名，不包含 HTTP 对象。
阶段二由 API 映射为 501，由 Runtime 通过 `trip_service` 依赖对接加载和保存；当前 Runtime 仍使用原 Repository 接线。

### 认证及公开 DTO

`auth_schema` 沿用现有内存登录校验、忽略未知字段、32 位字符串用户 ID 和错误响应。
`api_schema` 保留正式公开契约的严格字段、UUID 规范化、UTC、金额与 SSE 校验。
两者未合并；仅完成文件迁移不代表正式数据库认证或 HTTP/SSE 接入已经完成。

## 验证

- 原有默认离线测试迁移后通过：212 passed，2 deselected。
- 迁移前后 60 个既有 JSON Schema 逐项一致；5 段既有 Prompt 字符串逐字一致。
- 六表元数据注册顺序、ORM 关系和 MySQL DDL 保持一致，DDL 仅更新导出脚本路径注释。
- 新增 8 个测试覆盖新 API 入口的旧认证行为，以及短会话加载、错误传播、并发隔离和保存占位；最终默认回归为 **220 passed，2 deselected**。
- 92 个 Python 文件编译通过；全部 Schema 与 ORM 在禁止网络的进程内导入成功，未加载应用配置、日志、数据库工厂或模型客户端。
- 75 个前端文件与迁移前快照一致；实际源码无失效旧导入，Git diff 空白检查通过。
- 未运行真实模型、地图或数据库集成测试，未建表、未迁移数据库。

运行方式（工作目录均为项目根目录）：

```powershell
uv run pytest -q
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run python -m scripts.export_schema_sql
```

最后一条命令会更新建表 SQL 文件，但不连接数据库。PyCharm 使用项目现有虚拟环境，
测试运行器选择 pytest、目标目录选择 tests；服务运行配置使用模块 uvicorn，参数为 `app.main:app`。
指定监听地址和端口时使用 Uvicorn 参数；本阶段没有扩展全局 Settings 与资源生命周期接线。

## 阶段二边界

以下工作尚未执行，不作为阶段一完成项：

- Supervisor 的生产回退、需求 Agent、专业 Stub 的进一步拆分，以及 Runtime 改调 TripService。
- Planner/Search 的中间件、内部提交与证据检查拆分；费用规则提取到业务函数。
- 认证 Router/Service/存储分层、会话/运行/行程/事件接口和相应 501 骨架。
- 按需资源工厂、日志从导入初始化改为入口初始化、客户端所有权与关闭生命周期。
- 完整类型及中文注释清理；现有未实现专业能力仍保持原有状态。

阶段二文件所有权：主代理管理公共 Schema/Prompt/注册装配/API/服务/资源；
子代理 A 管主管与需求，B 管 Planner，C 管搜索与工具。跨边界改动统一由主代理协调。
