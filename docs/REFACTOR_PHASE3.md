# 重构阶段三：统一验收与日志接入

本阶段依据已批准的三阶段计划、阶段一/二交付记录，以及用户提供的两份规范：

- `C:/Users/Administrator/Documents/ChatGPT/知识点了解/Python应用包规范.md`
- `C:/Users/Administrator/Documents/ChatGPT/知识点了解/Python应用编码规范.md`，日志重点遵循第 16.2 节。

采用主代理 + 三个子代理，分别负责独立结构/契约验收、类型/中文文档检查、项目日志接入；主代理集成、修复确认问题并执行最终回归。基于当前未提交工作区继续，没有恢复 HEAD、修改前端、升级依赖、建表或提交 Git。

## 结果

**阶段三完成。最终离线回归：276 passed，2 deselected。**

本轮新增 12 项日志边界测试；完整测试命令为 `uv run pytest -q`。默认测试禁止真实外网，两个 integration 用例继续排除。本轮没有真实模型、地图、数据库或保存事务验证。

### 本轮确认并修复的问题

阶段三第一次复跑当前工作区得到 82 failed、182 passed、2 deselected。失败共因是 `WeatherImpactAgent` 继承冻结 dataclass `StubAgent`，却缺少子类 dataclass 装饰器，调用 `WeatherImpactAgent()` 仍要求三个构造参数，导致默认注册表初始化失败。该问题在本轮开始快照中已经存在；补回 `@dataclass(frozen=True)` 后，保留当前字段、权限和 run 骨架，最终全量通过。

日志专项另发现当前 FastAPI 的嵌套路由对象只保留局部路径，直接读取 `scope.route.path` 会丢失 `/api/v1` 前缀。现在通过公开 `iter_route_contexts()` 取得完整匹配模板；未知路径只记 `<unmatched>`，不记录用户路径和查询参数。

## 最终结构与调用方向

```text
app/main.py                         应用工厂、lifespan、注册装配
  api/*_router.py                   HTTP 请求与响应
  api/deps.py                      参数转换和依赖注入
  api/errors.py、middleware.py      错误映射、请求编号和摘要日志
    services/                      认证、旅行、费用、模型及地图业务能力
      repository/                  内存认证与数据库访问边界
        models/                    六张 ORM 表及关系，不创建连接

app/agent/
  requirement.py                   简单需求解析
  supervisor/                      图、State、上下文、决策、回退、Runtime
  planner/                         图、中间件、内部提交 output、证据 checks
  place_search/                    图、中间件、证据 checks
  accommodation/place_knowledge/weather_impact.py
                                   未实现专业能力的具名骨架
app/tools/                         六个公共工具、可信上下文、权限和额度
app/schemas/                       公开 DTO、内部业务模型、模型/工具协议
app/prompts/                       提示词字符串常量
app/core/                          配置、日志、模型/数据库工厂、延迟资源
scripts/verify_refactor.py          本轮新增的可重复只读验收脚本
```

Runtime 委托 `TripService` 加载和保存，继续管理权限、额度、保存前校验证明与状态合并。TripService 的每次查询创建独立短会话并在返回前关闭，推理阶段不持有 Session。保存方法继续抛出统一不可用异常。

API/Service/Repository 的公共参数、返回与异常说明补齐中文文档；配置、资源及关键变量说明用途、单位和可空语义。八个 Pydantic 校验器补 `Self` 返回类型。保持第三方原始响应、动态工具参数和状态补丁边界必要的 Any，不把静态注解当成外部输入校验。

**明确的规范兼容性取舍：**47 个原本没有 docstring 的 Pydantic 类补中文类前注释，而非新加类 docstring；后者会生成新的 JSON Schema `description`，违反冻结协议要求。66 个模型的 validation / serialization 共 132 份 JSON Schema 经子代理比较完全一致，主代理另外复核 66 份默认 Schema 与本轮开始快照一致。

## 日志实现

沿用 Loguru；不引入另一套日志框架。`core/log.py` 提供 `log_event()` 和 `safe_log_identifier()`，记录结构化 extra，同时在文本日志中保留可读的 event 与业务字段。

默认格式包含时间、级别、进程/线程、`request_id`、`run_id`、模块及调用位置。耗时字段为 `duration_ms`，以单调时钟计算；节点进入/退出仅 DEBUG，正常重要结果 INFO，预期拒绝/回退 WARNING，未恢复失败 ERROR，应用启动失败 CRITICAL。

| 边界 | 主要事件及内容 |
| --- | --- |
| HTTP | `http_response_started`、`http_unhandled_error`；方法、完整路由模板、状态码、请求编号、响应头耗时 |
| HTTP 拒绝 | `capability_unavailable`、`request_validation_rejected`；固定能力/原因码 |
| 认证 | `auth_completed/auth_rejected`；操作和内存结果，成功明确 `storage=memory` |
| 旅行服务 | `trip_load_completed` 在短会话关闭后记录；`trip_save_rejected` 不虚报保存成功 |
| 主管 | `agent_run_started/finished`、`agent_action_selected/completed`、`agent_decision_rejected`、`supervisor_rule_fallback` |
| Planner | `planner_model_completed`、`planner_completed`、`planner_rejected/failed`；模型轮次、受控动作、状态和毫秒 |
| 公共工具 | `tool_completed/tool_rejected`；工具名、调用标识、调用次数、状态和毫秒 |
| 外部调用 | `llm_invocation_completed`、`amap_retry/amap_completed`；操作、可见模型/尝试次数、超时和毫秒 |
| 生命周期 | `resource_initialized/resource_initialization_failed/resources_closed`、`application_started/application_start_failed/application_stopped` |

请求体、认证头、密码/邮箱/令牌、完整查询字符串、提示词、模型隐藏推理、工具原始参数/结果、上游原始异常不进入新增事件日志。技术标识限长并拒绝控制字符；SQLAlchemy 引擎启用 `hide_parameters=True`，减少参数进入驱动异常堆栈。异常栈仍交由约定服务器边界记录，新增事件不重复打印栈。

LLM 的 attempt=1 表示一次可观察的应用调用，不代表 SDK 内部只尝试一次；不可见的 SDK 重试和缺失 token 用量不伪造。高德事件记录客户端实际 HTTP 尝试次数。HTTP 耗时截止响应头，不表示未来 SSE 已推送完成。技术日志不替代持久化审计。

应用 lifespan 显式配置和关闭日志，模块导入不创建文件。默认路径为 `logs/app_YYYYMMDD.log`，本轮生命周期和离线回归已写入 `logs/app_20260922.log`；其中是实际离线执行日志，不是生产联调成果。日志文件保留在本机忽略目录中。

配置见 `.env.example` 的 LOG_* 项。仍按单进程、单应用生命周期使用文件轮转；多进程集中采集、同进程多应用重叠生命周期的日志隔离未新增实现。

## 验收证据

| 检查 | 最终证据 |
| --- | --- |
| `uv run pytest -q` | **276 passed，2 deselected，19.51s** |
| `uv run python -m scripts.verify_refactor` | 通过，116 个 Python 文件编译成功 |
| 显式模块初始化依赖 | 87 个应用模块、197 条边，无环；排除 22 条 TYPE_CHECKING 和 11 条函数局部延迟导入 |
| 失效旧路径 | 源码、测试、脚本的导入语法扫描无残留；历史迁移文档保留旧路径说明 |
| 独立进程导入 | 9 个 Schema 模块及 ORM 成功；禁止网络、配置、日志、模型及数据库资源模块初始化 |
| ORM 与 DDL | 六表 mapper、字段/关系/约束回归通过；生成 SQL 与现有 SQL 完全一致 |
| 冻结文件 | 前端、ORM、Prompt、DDL、pyproject.toml 和 uv.lock 共 93 个文件与本轮开始快照逐字节一致 |
| Schema | 66 个模型默认 JSON Schema 相同；子代理另验证 132 份双模式 Schema 相同 |
| 注解完整性 | app 中 176 个公共/非下划线函数均具备参数与返回注解 |
| 日志专项 | 新增 12 项覆盖请求隔离、模板、脱敏、认证结果、运行/工具关联、重试及关闭失败 |
| `git diff --check` | 通过；仅现有 Windows LF/CRLF 提示 |

静态依赖扫描只判断显式模块初始化依赖，不声称穷尽动态导入。未安装或运行 mypy/pyright，没有新增开发依赖；注解完整性与编译通过不等于全量静态类型正确性证明。动态装配、图节点、并发隔离及失败边界以完整离线测试验证。

## 当前未实现能力

- 住宿、景点知识、天气专业 Agent 仍返回 `unimplemented`。
- 行程保存事务、版本并发控制、来源运行幂等和真实写入仍未实现。
- 会话/运行/事件真实存储、后台调度、恢复、SSE 推送仍未实现。
- 正式持久认证、业务接口归属验证、游标数据语义和公开结果投影仍待接入。
- 10 项新增业务 HTTP 操作的合法请求返回 **501 / CAPABILITY_UNAVAILABLE**；SSE 在建流前返回普通 JSON 错误。不会创建后台任务或访问真实业务数据。
- 注册登录继续采用既有内存存储、PBKDF2、临时签名密钥、响应及错误行为，不能称为正式持久认证。

README、API_CONTRACT 与 DATABASE_MODEL 已同步当前事实。阶段一/二报告保留各阶段历史，不将过去的占位状态改写成当时已实现。

## PyCharm 与运行方式

工作目录统一为项目根目录，解释器为 `.venv/Scripts/python.exe`：

- 服务：Python 配置选择模块 `uvicorn`，参数 `app.main:app --host 127.0.0.1 --port 8000`。
- 测试：默认运行器 pytest，目录 `tests`；integration 继续单独选择，不使用真实凭据做默认单测。
- 验收：模块 `scripts.verify_refactor`，只读检查，不执行 DDL。

```powershell
uv run python -m scripts.verify_refactor
uv run pytest -q
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Uvicorn 监听配置以 CLI 参数为准，APP_HOST/APP_PORT 不自动覆盖 CLI。真实资源由首次使用时创建，应用退出关闭自有对象；外部注入资源由调用方关闭。
