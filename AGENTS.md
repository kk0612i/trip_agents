# Python 应用开发规则

本文件供 Codex 在 Python 应用项目中执行开发、修改和代码审查时遵守。适用技术栈：Python、FastAPI、SQLAlchemy、Pydantic、LangChain、LangGraph。

## 1. 工作方式与规范来源

- 默认使用中文沟通、编写业务注释和文档字符串。
- 修改前检查实际目录、依赖版本、入口、调用关系和相关测试，不凭教学示例推断现有实现。
- 先判断用户是在讨论、审查还是要求实现。用户要求先讨论时，不修改文件；明确要求实现后，完成范围内的工作与必要验证。
- 保留正确代码和用户现有修改。新增代码遵守本规范，不为了统一风格顺带重构无关模块、批量重命名或升级依赖。
- 按任务需要创建文件，不预建空包，不机械引入接口、基类、注册表、Unit of Work 或复杂架构。
- 可按需查阅同目录的 `Python应用包规范.md` 和 `Python应用编码规范.md`，不必每次完整读取。复制本文件到其他项目后，若参考文档不在项目中，直接使用本文件的规则。
- 两份参考文档存在旧例子差异时，本文件采用已确认的约定：`agent` 为单数包名、图文件保留 `_graph.py`、工具文件无 `_tool` 后缀、Repository 依赖使用 `_repo`、提示词使用纯字符串常量。
- 规范中的默认写法不得覆盖用户本次明确要求；已有项目的接口、数据和运行行为不可仅为风格调整而改变。

## 2. 包职责与文件命名

顶层按职责分包，复杂 Agent 内部按功能分子包。

| 位置 | 文件名示例 | 职责 |
| --- | --- | --- |
| `app/main.py` | `main.py` | FastAPI 应用创建、生命周期、路由和异常处理器注册 |
| `app/api/` | `user_router.py` | HTTP 参数、依赖、状态码和响应 |
| `app/api/` | `deps.py`、`errors.py` | 请求依赖组装、业务异常到 HTTP 的映射 |
| `app/services/` | `user_service.py` | 业务规则、操作协调和事务边界 |
| `app/repository/` | `user_repository.py` | SQL 查询、写入、更新和删除 |
| `app/models/` | `user.py`、`base.py` | ORM 表映射、统一 Base 和公共 Mixin |
| `app/schemas/` | `user_schema.py` | 请求、响应、业务传输和模型结构化输出 |
| `app/core/` | `config.py`、`db.py`、`log.py`、`errors.py`、`llm.py` | 配置、引擎与会话工厂、日志、异常定义、模型对象创建 |
| `app/agent/` | `chat_graph.py`、`chat_state.py` | 图构建与自定义状态 |
| `app/prompts/` | `chat_prompt.py` | 提示词字符串常量 |
| `app/tools/` | `order.py`、`search.py` | Agent 工具及业务调用适配，不加 `_tool` 后缀 |
| `tests/` | `test_user_service.py` | 测试及测试替身 |
| `scripts/`、`docs/` | 按实际用途命名 | 维护脚本和项目文档 |

- 普通 Python 包保留 `__init__.py`，初期可以为空；不要在其中集中导入所有模块造成循环依赖。
- ORM Base 放在 `models/base.py`，数据库引擎和会话工厂放在 `core/db.py`，不另建重复的 `db/session.py`。
- 私有实现就近放置，共享需求明确后再提取；不把无关逻辑集中到 `utils.py`、`common.py`。
- 小功能先用文件，职责复杂后再拆子包，不要求每层同时拆，不要求每张表对应一个业务模块。
- 不用依赖库名称建立 `langchain/`、`langgraph/` 业务包；`models` 专指 ORM，不存放大语言模型客户端。

## 3. 类、函数和依赖注入

- 多个方法需要共同使用 Session、Repository 或模型依赖时，优先使用类。
- 纯计算、转换、判断、路由函数、图构建函数优先使用普通函数，不制造只有静态方法的工具类。
- 节点优先使用函数；多个节点确需共享依赖时可以用类的方法，但不得在共享实例上保存本轮请求状态。
- ORM、Schema、自定义框架 Middleware 按框架要求使用类。
- 构造函数只保存依赖和做轻量校验，不查询数据库、不访问网络、不调用模型，不定义异步 `__init__`。
- 稳定依赖通过构造函数传入；当前操作的 ID、输入和消息通过方法参数传入。
- Repository 依赖参数及字段统一为 `user_repo`、`order_repo`、`audit_repo`，使用 `self.user_repo = user_repo`，禁止 `self.users = users` 等含糊命名。
- Service 依赖命名为 `user_service` 等，数据库会话命名为 `session`。
- 在 `api/deps.py` 或明确的应用装配位置组装依赖；业务层不使用 FastAPI `Depends`。
- 不给每个类机械增加 Protocol 或抽象基类；需要替换实现、隔离边界或测试契约时再引入。

依赖命名示例（片段，类型由所在模块导入）：

```python
class UserService:
    """协调用户业务与审计操作。"""

    def __init__(
        self,
        user_repo: UserRepository,
        audit_repo: AuditRepository,
    ) -> None:
        """保存当前业务工作单元的数据访问依赖。

        Args:
            user_repo: 用户查询与写入对象。
            audit_repo: 与 user_repo 共用当前会话的审计对象。
        """
        # 用户表查询与写入能力。
        self.user_repo = user_repo
        # 审计写入能力，由外层业务事务统一提交。
        self.audit_repo = audit_repo
```

## 4. 类型、变量注释与文档字符串

- 类名使用 `PascalCase`，函数、变量和文件使用 `snake_case`，常量使用 `UPPER_SNAKE_CASE`。
- 跨模块函数和方法写参数、返回值类型，明确 `None` 的含义；避免用 `Any` 和无结构字典代替稳定契约。
- 参数较多或含义相近时优先使用关键字参数。
- 可变集合默认值使用 `default_factory` 或在函数内部创建；不使用共享的 `[]`、`{}` 默认参数。
- 定义变量时添加中文含义说明，覆盖依赖字段、配置项、ORM/Schema 字段、状态字段和重要中间结果。说明角色、单位、来源和可空含义，不只重复类型或赋值动作。
- 类、公共函数、公共方法使用中文 Google 风格文档字符串。私有函数至少说明用途，复杂逻辑同样写完整说明。
- 文档按需包含 `Args`、`Returns`、`Raises`；生成器用 `Yields`；构造函数通常说明用途和 `Args`。不添加无内容的段落。
- `Raises` 必须符合真实异常行为；不要声明不存在的异常类型，或为了文档添加无意义的捕获。
- 关键事务、并发、权限、资源生命周期和证据约束添加原因注释；修改代码时同步更新注释。

Repository 方法文档示例（类内片段）：

```python
async def get_by_id(self, user_id: int) -> User | None:
    """根据用户 ID 查询用户。

    Args:
        user_id: 数据库中的用户主键。

    Returns:
        找到用户时返回 User，否则返回 None。

    Raises:
        SQLAlchemyError: 数据库访问失败时传播原异常。
    """
    # 查询结果；主键不存在时为 None，查询失败直接传播异常。
    user = await self.session.get(User, user_id)
    return user
```

## 5. 同步、异步与并发

- 需要等待异步数据库、HTTP、模型或图调用的方法使用 `async def` 并 `await`；上层等待这些方法时也使用异步。
- 纯计算、字段转换、构造函数、条件路由和普通图编译使用 `def`；短小同步函数可以直接在异步函数里调用。
- `AsyncSession.add()` 不加 `await`；查询、`flush()`、`commit()` 等按真实 API 使用 `await`，不能仅凭对象是异步类型推断所有方法都异步。
- 异步链路优先使用异步客户端、模型 `ainvoke()`、图 `ainvoke()`/`astream()`。
- 不在事件循环中直接执行阻塞 I/O；只有同步接口时可交给线程，CPU 密集任务考虑进程或任务队列。
- FastAPI 只自动调度它直接调用的同步路由和同步依赖；异步函数中自行调用的同步辅助函数不会自动进入线程池。
- 不在异步路由或节点中调用 `asyncio.run()`。
- 同一个 `AsyncSession` 不用于并发任务。`gather()` 前确认依赖支持并发，独立 Session 不自动组成同一个事务。
- 明确超时、重试和取消边界；不吞掉 `CancelledError`，资源通过上下文管理器或 `finally` 释放。等待超时不保证远端写入或线程工作已停止。

## 6. 各层实现边界

### API

- Router 只负责请求校验、可信身份与依赖获取、Service 调用、HTTP/SSE 响应。
- 声明输入输出 Schema 与状态码，不在 Router 编写 SQL、事务流程或完整 Agent 编排。
- 业务异常在 API 边界统一映射；不要每个路由捕获所有异常并返回 HTTP 200。

### Service

- 实现业务规则，协调多个 Repository，明确最外层业务工作单元的事务所有权。
- 不依赖 `Request`、`Depends`、`HTTPException`，不自行读取环境变量或创建隐藏的外部客户端。
- 入口 Service 可调用 Agent；Agent/Tool 可调用独立业务 Service。被调用的业务 Service 不反过来调用当前 Agent 或入口 Service。
- 传出会话边界前按需要将 ORM 转为明确的业务或响应 Schema。

### Repository

- 使用注入的 Session，只执行数据操作，不自行创建或关闭 Session，不自行提交事务；需要主键等数据时可以 `flush()`。
- 单条未找到返回 `None`，集合无结果返回空列表；数据库失败传播异常，不伪装成无数据。
- 使用参数绑定；动态排序字段使用允许列表。列表查询明确排序和条数边界。
- 不处理 HTTP、不做模型决策、不复制业务规则。不把所有 `IntegrityError` 都翻译成同一种业务冲突。

### Models 与 Schemas

- ORM 继承统一 `Base`，明确字段、可空性、约束和索引；精确金额使用 `Decimal`/`Numeric`。
- ORM 不负责连接、提交和外部请求；迁移配置导入全部模型后使用元数据，不在应用启动时自动修改表结构。
- Schema 校验结构、格式、范围和无需外部 I/O 的字段一致性；数据库依赖的业务检查交给 Service。
- 明确必填、可省略、可空的区别。更新使用 `exclude_unset` 等区分未传与显式 null，不能用 `value or old_value` 统一处理。
- 使用 `from_attributes=True` 转换 ORM 时，先加载必要字段和关系；响应序列化阶段不能触发异步懒加载。

## 7. Session、事务与生命周期

- 引擎、会话工厂和可并发的模型客户端可在进程内复用；Session 及持有它的 Service/Repository 按请求或工作单元创建。
- 一组需要共同提交的 Repository 必须使用同一个 Session，并在同一个事务中顺序执行。
- 最外层业务操作统一提交或回滚，成功提交后才返回成功。内部方法不得擅自提交外层事务。
- `api/deps.py` 管理请求 Session 的提供和关闭，不把关键提交延迟到 `yield` 之后的清理阶段。
- 开启事务前检查调用链是否已经使用 Session。查询也会触发 autobegin，鉴权先查询再无条件 `begin()` 可能冲突。
- 明确选择入口拥有事务或复用外层事务，不用 `in_transaction()` 静默掩盖边界，也不用 `begin_nested()` 随意修复事务冲突。
- 模型推理、人工暂停和长时间外部调用不占用长数据库事务。并发唯一性和库存等依靠数据库约束、条件更新或必要的锁保证。
- `main.py` 生命周期负责资源初始化与关闭，`create_app()` 负责组装 HTTP 组件；模块导入和构造函数不得执行外部 I/O。
- 不修改 `sys.path` 解决包导入；使用项目解释器、项目根工作目录和正确的模块入口。

## 8. Agent 与主管架构

- 简单 Agent 用文件，复杂 Agent 用子包，如 `agent/planner/planner_graph.py`。
- `_graph.py` 负责图或预置 Agent 的构建与调用协调；自定义状态使用 `_state.py`，节点复杂后才拆 `_nodes.py`。
- 需要时就近放置 `middleware.py`、`checks.py`、`context.py`；不为目录对齐创建无用途文件。
- 图状态、ORM、Schema 分开。运行资源、密钥、Session 不进入可持久化图状态。
- 节点返回自己负责的更新，不原地修改共享嵌套数据。明确 reducer 语义，追加 reducer 只返回增量，避免重复历史。
- 并行写同一字段必须有合适的合并规则。复用图不得捕获请求级 Session、用户身份或可变工具账本。
- 模型结构化输出用 Schema 校验。循环、模型次数、工具预算和 `recursion_limit` 分别明确；重试与恢复后的写入使用稳定幂等标识。
- 主管的 `decision.py` 决定动作，`runtime.py` 校验并执行动作、维护预算与状态，`supervisor_graph.py` 组装流程。只有需要自定义这些职责时才建立文件。
- 子 Agent 有独立状态时，明确转换输入输出；共享业务规则交给 Service，专有证据检查保留在 Agent 内部。
- 模型选择不构成授权：工具权限、用户归属、写入前置条件和 checkpoint 会话归属由服务端代码检查。
- 测试 Fake Agent 放在 `tests`；生产中实际使用的回退或占位实现不能误移到测试目录。

## 9. Prompts 与 Tools

- `prompts` 只定义三引号字符串常量及变量说明，不导入 LangChain，不构造 `ChatPromptTemplate`、Runnable 或模型。
- 系统提示词用 `XX_SYSTEM_PROMPT`，用户模板用 `XX_USER_PROMPT`。在使用它的 Agent/调用模块中创建模板、绑定工具、指定结构化输出。
- 用户输入作为模板变量填入，不先拼成模板再解析；需要字面花括号时按实际模板规则转义，不二次格式化已填充文本。

```python
# 订单助手系统规则；实际数据权限仍由业务代码检查。
ORDER_SYSTEM_PROMPT = """
你是订单助手。
只能依据工具返回的订单信息回答，不得编造订单状态。
"""

# question 为本轮用户问题，由调用方填入。
ORDER_USER_PROMPT = """
用户问题：
{question}
"""
```

- 工具文件不加 `_tool` 后缀；简单工具用函数，依赖通过工厂或运行上下文注入。
- 工具名称、描述、参数和结果明确，委托业务 Service 或外部能力执行，不复制业务规则。
- 当前用户身份、Session、权限上下文不作为模型可填的授权参数。捕获用户身份的工具不得跨用户共享。
- 固定步骤直接调用普通函数或 Service，不强制包装成工具。Agent 私有结果提交工具可就近放在 `output.py`。
- 可恢复工具失败按明确契约处理；拒绝、失败不能伪装成成功空结果。副作用操作考虑重复执行和超时后的结果不明。

## 10. 日志与错误处理

- 使用项目已有 logging 或 Loguru，在 `core/log.py` 集中配置，不另建第二套。模块内获取 Logger，不重复添加 Handler。
- API 记录请求方法、路由模板、状态码、request_id 和耗时；Service 记录关键业务结果；Repository 仅在必要时 DEBUG 排查。
- Agent 记录 run_id、节点、动作、目标、轮次与终态；Tool 记录工具名、调用 ID、状态和耗时；外部调用记录尝试次数、超时与可用的用量。
- INFO 记录正常关键阶段；WARNING 记录重试、降级、预算耗尽；ERROR 记录未恢复的意外失败。普通输入错误和资源不存在不全记为 ERROR。
- 使用稳定事件名及字段：`event`、`request_id`、`run_id`、`status`、`duration_ms`、`attempt`、`error_type`。耗时使用单调时钟，单位明确。
- 请求关联标识通过 ContextVar 或显式参数传递，不用全局可变字段保存；后台或跨服务调用显式传播标识。
- 事务提交后才记业务成功，`flush()` 不代表成功提交。技术日志不能替代数据库业务审计。
- 不输出密码、密钥、连接 URL、认证头、完整请求体、提示词、消息历史、模型隐藏推理或原始工具响应。异常原文也要考虑 SQL 参数等敏感信息。
- logging 使用参数化 `%s` 格式，Loguru 使用自身格式；不要混用占位符。生产日志按实际环境配置结构化输出、轮转和保留。
- 意外异常堆栈由一个明确边界负责，避免逐层重复打印；HTTP 错误与服务器日志协调，Worker 由任务入口记录。
- 捕获能够恢复或需要转换的具体异常，转换使用 `raise ... from exc`；不要捕获所有异常返回 `None`、空列表或成功标志。
- SSE 响应开始日志不等于流全部完成；完整流耗时需要跟踪结束、断连与取消。

## 11. 验证与交付

- 使用项目现有解释器、依赖和验证方式；不自行引入 Ruff 等用户未选择的工具，不将全部检查工具强加为必需依赖。
- 用户以 PyCharm 为主，运行说明优先给出模块、工作目录、解释器和环境变量设置；执行验证可以使用现有命令行工具，不要求用户改用命令行开发。
- 测试重点是业务分支、失败边界、权限范围、事务原子性、图路由和重试/恢复行为；不为低影响的格式或文档改动编写复制实现的测试。
- 普通单元测试使用可控模型和外部服务替身；数据库、真实模型集成测试单独运行并明确环境，不用业务数据库做破坏性测试。
- 涉及多 Repository 的事务修改，应验证某一步失败时不会留下部分记录；只检查 `commit()` 调用次数不足以证明原子性。
- 修改目录时同步更新导入、装配、测试引用和必要文档，尽量与业务行为修改分开。
- 交付前核对：文件归属和后缀、`_repo` 命名、类型与中文注释、提示词常量、依赖与事务边界、异步资源、日志字段及敏感内容。
- 汇报实际修改、实际执行的检查及未验证部分；语法检查不能表述为集成测试通过，替身测试不能表述为真实模型可用。
