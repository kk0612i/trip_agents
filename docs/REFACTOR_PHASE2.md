# 重构阶段二：并行职责拆分

本阶段以阶段一的未提交工作区为基线，采用 **1 个主代理 + 3 个子代理**，按文件所有权并行实现。没有恢复 HEAD、清理既有改动、提交 Git 或进入阶段三。

依据用户确认的方案、`REFACTOR_PHASE1.md`，以及本机 `知识点了解/Python应用包规范.md`、`知识点了解/Python应用编码规范.md`。提示词采用已确认的“仅字符串常量”约定。

## 完成范围

| 执行者 | 实际交付 | 边界 |
| --- | --- | --- |
| 子代理 A | 主管决策与生产回退分离；需求 Agent、Stub 分离；住宿、知识、天气具名占位；Runtime/Context/Graph 改用 TripService | 权限、预算、校验凭证、状态合并仍由 Runtime 管理 |
| 子代理 B | Planner 图组装、协议中间件、内部提交、证据验收分离 | `_PlannerResult` 名称和内部提交不占公共工具额度保持不变 |
| 子代理 C | 搜索图、协议中间件、证据验收分离；费用规则提为普通函数；工具、高德类型及注释整理 | `_SearchDecision`、工具权限和未知价格语义保持不变 |
| 主代理 | 注册装配、认证分层、10 项业务 HTTP 骨架、Service/Repository 占位、错误映射、资源生命周期与集成验证 | 不实现真实保存、调度、推送或新专业能力 |

## 目录与职责

```text
app/
  agent/
    base.py                  # SpecialistAgent、DecisionProvider 协议
    registry.py              # 名称、权限目录和默认装配
    requirement.py           # 简单需求 Agent 和生产离线解析
    stub.py                  # 未实现能力公共结果骨架
    accommodation.py         # AccommodationSearchAgent
    place_knowledge.py       # PlaceKnowledgeAgent
    weather_impact.py        # WeatherImpactAgent
    supervisor/
      supervisor_graph.py    # 节点、边、检查点接线
      supervisor_state.py    # State 和 reducer
      context.py             # 注入 TripService 等依赖
      decision.py            # 模型决策
      fallback.py            # 生产规则回退
      runtime.py             # 权限、预算、验证凭证和状态合并
    planner/
      planner_graph.py        # 图构建和单轮编排
      middleware.py          # 模型及工具协议
      output.py              # 内部最终结果提交
      checks.py              # 地点、路线和费用证据验收
      errors.py              # 安全业务异常
    place_search/
      place_search_graph.py  # 图组装和运行
      middleware.py          # 搜索协议检查
      checks.py              # 搜索证据及推荐事实验收
  api/
    auth_router.py           # 原认证 HTTP 行为
    session_router.py        # 会话及消息骨架
    run_router.py            # 状态和事件骨架
    trip_router.py           # 旅行及版本骨架
    deps.py                  # HTTP 参数和应用服务依赖
    errors.py                # 业务异常到 HTTP 的映射
    middleware.py            # 请求编号和缓存策略
  services/
    auth_service.py          # PBKDF2 和令牌业务
    session_service.py       # 会话能力占位
    run_service.py           # 受理、历史、状态和事件占位
    trip_service.py          # 真实内部短会话读取；保存和公开查询占位
    cost_service.py          # 不依赖 ToolRuntime 的普通费用函数
    llm_service.py           # 需求解析和主管决策；移除空 plan_itinerary
  repository/
    auth_repository.py       # 独立内存用户存储
    session_repository.py    # 会话存取占位
    run_repository.py        # 运行及事件存取占位
    trip_repository.py       # 原读取查询及明确的待实现方法
  core/
    db.py                    # 引擎、短会话工厂和 SessionFactory 类型
    llm.py                   # 模型工厂
    resources.py             # 应用延迟资源、所有权及释放
    log.py                   # 入口显式配置/关闭日志
  main.py                    # create_app、lifespan、路由和处理器注册
```

`specialists.py` 已删除，不保留旧路径兼容层。Planner/Search 的证据模块最终统一为规范中的 `checks.py`，内部提交使用 `output.py`。

## 公共接线变化

- `AgentRuntime` 和 `AutonomousGraphContext` 使用 `trip_service`，删除 `repository` 参数；Graph 构建函数同时支持 `trip_service=` 快捷注入。
- `TripService.load_current()` 保留查询及异常传播，返回前关闭本次会话；已有测试证明下一步主管决策时会话已经退出。
- `TripService.save_version()` 仍抛出 `CapabilityUnavailableError`，不会打开会话、提交事务或产生虚假版本。
- `core/db.py` 使用 `create_engine()`、`create_session_factory(engine)`，不再使用进程全局缓存引擎。
- `AppResources(session_factory=..., llm=..., amap_client=...)` 支持外部注入；只登记自建资源的关闭回调。`session_factory`、`llm`、`amap_client` 均按需初始化。
- API 依赖负责组装 Service；core 资源容器不反向导入业务 Service。独立脚本使用容器后须 `await resources.aclose()`。
- `get_llm()` 不再缓存；应用容器负责复用。独立调用该工厂的脚本须管理底层客户端生命周期。
- 日志不再在导入时读取配置、创建目录或文件；应用 lifespan 启动配置、退出清理。只移除 Loguru 默认输出及应用登记的 sinks，保留宿主/测试额外注入的接收器。

冻结的 Agent 名称、说明、六个公共工具、权限集合、模型协议、Graph 节点、State 字段与 reducer、每轮清理、未知价格及保存前复核语义未改变。公共 Schema、Prompt 和 ORM 文件与本阶段基线逐字节一致。

## HTTP 骨架

已注册以下 10 项业务操作，OpenAPI 声明目标成功 DTO 和当前 501 错误 DTO：

| 方法 | 路径（统一前缀 `/api/v1`） |
| --- | --- |
| POST / GET | `/sessions` |
| GET | `/sessions/{session_id}` |
| POST / GET | `/sessions/{session_id}/runs` |
| GET | `/runs/{run_id}` |
| GET | `/runs/{run_id}/events` |
| GET | `/trips/{trip_id}` |
| GET | `/trips/{trip_id}/versions` |
| GET | `/trips/{trip_id}/versions/{version_no}` |

合法占位请求返回 **501 + CAPABILITY_UNAVAILABLE**，使用既有 `ErrorResponse`；`request_id` 与 `X-Request-Id` 一致，日志记录同一编号。会话、运行、事件响应包含 `Cache-Control: no-store`。

SSE 在创建流式响应之前返回普通 JSON 错误，绝不提前返回 200 或发送虚假事件。UUID、旅行编号、版本号、分页和 Last-Event-ID 先校验；非法业务参数返回 422，非法 JSON 返回 400。

正式 Bearer 身份验证和归属检查仍待实现，因此占位接口只拒绝能力，不读取真实业务数据。未知资源没有伪造的 404 查询结果。未来开放成功路径之前必须先接入身份验证。

注册登录仍使用独立 `auth_schema`：邮箱规范化、密码首尾空白、额外字段宽松行为、32 位用户 ID、240000 次 PBKDF2-SHA256、七天签名令牌和原有 `detail` 错误保持一致。每个应用实例拥有内存用户存储和临时签名密钥，未改成正式数据库认证。

## 验证证据

工作目录为项目根目录。

| 检查 | 结果 |
| --- | --- |
| 阶段二开始前 `uv run pytest -q` | 220 passed，2 deselected |
| A：主管、具名占位及 TripService 定向回归 | 70 passed |
| B：Planner 及自主工具选择 | 32 passed，2 deselected |
| C：搜索、工具、费用和高德 | 43 passed |
| API 定向回归（包含原认证测试） | 27 passed |
| 资源/导入生命周期专项 | 8 passed |
| 最终 `uv run pytest -q` | **264 passed，2 deselected** |
| 编译 app、tests、scripts | **114 个 Python 文件通过** |
| 旧导入/旧 repository 注入扫描 | 源码、测试、脚本无匹配 |
| 阶段二冻结快照比较 | 102 个文件无内容差异；覆盖前端、Schema、Prompt、ORM、DDL、pyproject.toml、uv.lock |
| ORM 离线检查 | 六表元数据保留，既有模型/DDL 回归通过 |
| `git diff --check` | 通过 |

共新增 44 项测试，重点验证协议拒绝、工具额度、失败证据、短会话、501/SSE、参数错误和资源所有权。生命周期还覆盖模型创建失败、单项关闭失败后继续清理、日志启动失败仍清理和 LOG_ENABLED=false 不保留默认输出。

默认测试禁止真实外网；真实模型、地图和数据库均未验证。没有建库、迁移或真实事务写入。没有执行静态类型检查器，类型补齐通过源代码审阅与运行回归检查，不能将其称为全量静态类型验收。

## 剩余占位和审核点

- 住宿搜索、景点知识、天气专业 Agent 仍返回既有 `unimplemented` 结果。
- 保存事务、版本冲突的真实持久化及来源运行幂等仍未实现。
- 会话/运行存储、后台调度、运行恢复、事件保存与 SSE 推送仍未实现。
- 正式认证、用户归属检查、游标数据语义及业务结果投影仍未实现。
- 日志按单进程单应用生命周期配置；同进程多个应用并行生命周期的独立日志隔离、多进程聚合不在本阶段范围。
- 全仓统一类型/中文文档复核、循环导入检查和最终验收资料归阶段三。本报告只交付阶段二及其必要回归。

启动方式保持 `uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`。PyCharm 使用项目虚拟环境，工作目录为项目根目录，模块 uvicorn、参数 `app.main:app`。

**阶段二实现和回归完成，等待用户审核；未进入阶段三。**
