-- 从 SQLAlchemy ORM 生成；仅供新库使用，不包含删除或迁移语句。

-- 更新方式：uv run python -m scripts.export_schema_sql

-- 应用数据库连接也必须使用 UTC 会话时区。

SET time_zone = '+00:00';

CREATE TABLE app_user (
	id VARCHAR(36) NOT NULL COMMENT '用户 UUID',
	email VARCHAR(254) NOT NULL COMMENT '去首尾空白并转小写的邮箱',
	password_hash BLOB(32) NOT NULL COMMENT 'PBKDF2-SHA256 密码哈希',
	password_salt BLOB(16) NOT NULL COMMENT '随机密码盐',
	created_at DATETIME(6) NOT NULL COMMENT 'UTC 创建时间' DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT uk_app_user_email UNIQUE (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户账号';

CREATE TABLE trip (
	id BIGINT UNSIGNED NOT NULL COMMENT '旅行编号' AUTO_INCREMENT,
	user_id VARCHAR(36) NOT NULL COMMENT '旅行所有者 UUID',
	request_json JSON NOT NULL COMMENT '用户旅行需求',
	current_version_id BIGINT UNSIGNED COMMENT '当前使用的行程版本编号',
	created_at DATETIME(6) NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL COMMENT '修改时间' DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT fk_trip_user FOREIGN KEY(user_id) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='旅行主表';

CREATE INDEX idx_trip_current_version_id ON trip (current_version_id);

CREATE INDEX idx_trip_user_id ON trip (user_id);

CREATE TABLE chat_session (
	session_id VARCHAR(36) NOT NULL COMMENT '会话 UUID',
	user_id VARCHAR(36) NOT NULL COMMENT '会话所有者 UUID',
	trip_id BIGINT UNSIGNED COMMENT '首次保存前为空',
	trip_request_json JSON COMMENT '最近已提交的完整需求快照',
	pending_question TEXT COMMENT '尚未回答的问题',
	latest_run_id VARCHAR(36) COMMENT '最近已发布终态的运行 UUID',
	active_run_id VARCHAR(36) COMMENT '正在排队或执行的运行 UUID',
	created_at DATETIME(6) NOT NULL COMMENT 'UTC 创建时间' DEFAULT CURRENT_TIMESTAMP(6),
	updated_at DATETIME(6) NOT NULL COMMENT 'UTC 更新时间' DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
	PRIMARY KEY (session_id),
	CONSTRAINT fk_chat_session_user FOREIGN KEY(user_id) REFERENCES app_user (id),
	CONSTRAINT fk_chat_session_trip FOREIGN KEY(trip_id) REFERENCES trip (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='旅行会话';

CREATE INDEX idx_chat_session_user_created ON chat_session (user_id, created_at, session_id);

CREATE TABLE agent_run (
	id BIGINT UNSIGNED NOT NULL COMMENT '内部提交序号' AUTO_INCREMENT,
	run_id VARCHAR(36) NOT NULL COMMENT '公开运行 UUID',
	session_id VARCHAR(36) NOT NULL COMMENT '所属会话 UUID',
	client_request_id VARCHAR(36) NOT NULL COMMENT '客户端逻辑提交 UUID',
	request_hash VARCHAR(64) NOT NULL COMMENT '规范化请求 SHA-256',
	message TEXT NOT NULL COMMENT '用户输入，规范化后 1 至 4000 字符',
	expected_version_no INTEGER UNSIGNED COMMENT '客户端期望版本',
	base_version_no INTEGER UNSIGNED COMMENT '本轮实际加载的版本',
	status VARCHAR(20) NOT NULL COMMENT 'queued/running/completed/needs_input/failed',
	intent VARCHAR(20) COMMENT '已识别意图，识别前为空',
	response TEXT COMMENT '面向用户的最终回答',
	pending_question TEXT COMMENT '本轮追问',
	missing_fields_json JSON NOT NULL COMMENT '缺失字段列表，初始为空数组',
	result_json JSON NOT NULL COMMENT '公开 RunResult，初始六个字段均为 null',
	error_json JSON COMMENT '公开错误，正常时为空',
	graph_run_id VARCHAR(36) COMMENT '内部 Graph 运行编号，不向客户端公开',
	created_at DATETIME(6) NOT NULL COMMENT 'UTC 受理时间' DEFAULT CURRENT_TIMESTAMP(6),
	started_at DATETIME(6) COMMENT 'UTC 开始时间',
	finished_at DATETIME(6) COMMENT 'UTC 终态发布时间',
	PRIMARY KEY (id),
	CONSTRAINT uk_agent_run_run_id UNIQUE (run_id),
	CONSTRAINT uk_agent_run_submission UNIQUE (session_id, client_request_id),
	CONSTRAINT fk_agent_run_session FOREIGN KEY(session_id) REFERENCES chat_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='消息及 Agent 运行';

CREATE INDEX idx_agent_run_session_order ON agent_run (session_id, id);

CREATE INDEX idx_agent_run_status_created ON agent_run (status, created_at);

CREATE TABLE itinerary_version (
	id BIGINT UNSIGNED NOT NULL COMMENT '行程版本编号' AUTO_INCREMENT,
	trip_id BIGINT UNSIGNED NOT NULL COMMENT '所属旅行编号',
	version_no INTEGER UNSIGNED NOT NULL COMMENT '版本号，例如 1、2、3',
	source_run_id VARCHAR(36) NOT NULL COMMENT '来源公开运行 UUID，一次运行最多保存一个版本',
	routes_json JSON NOT NULL COMMENT '该版本的路线快照',
	content_hash VARCHAR(64) NOT NULL COMMENT '版本内容 SHA-256，用于重复保存比对',
	request_snapshot_json JSON NOT NULL COMMENT '生成该版本时的需求快照',
	itinerary_json JSON NOT NULL COMMENT '结构化行程内容',
	validation_json JSON COMMENT '行程校验结果',
	total_cost NUMERIC(12, 2) COMMENT '预计总花费',
	created_at DATETIME(6) NOT NULL COMMENT '版本创建时间' DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (id),
	CONSTRAINT uk_itinerary_version_trip_version UNIQUE (trip_id, version_no),
	CONSTRAINT uk_itinerary_version_source_run UNIQUE (source_run_id),
	CONSTRAINT fk_itinerary_version_trip FOREIGN KEY(trip_id) REFERENCES trip (id) ON DELETE CASCADE ON UPDATE CASCADE,
	CONSTRAINT fk_itinerary_version_run FOREIGN KEY(source_run_id) REFERENCES agent_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='行程版本表';

CREATE INDEX idx_itinerary_version_trip_id ON itinerary_version (trip_id);

CREATE TABLE run_event (
	run_id VARCHAR(36) NOT NULL COMMENT '公开运行 UUID',
	seq INTEGER UNSIGNED NOT NULL COMMENT '运行内从 1 递增的事件序号',
	event_type VARCHAR(24) NOT NULL COMMENT 'SSE 命名事件类型',
	data_json JSON NOT NULL COMMENT '公开事件 data，不包含内部 trace',
	occurred_at DATETIME(6) NOT NULL COMMENT 'UTC 事件时间' DEFAULT CURRENT_TIMESTAMP(6),
	PRIMARY KEY (run_id, seq),
	CONSTRAINT fk_run_event_run FOREIGN KEY(run_id) REFERENCES agent_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='运行进度事件';

ALTER TABLE trip ADD CONSTRAINT fk_trip_current_version FOREIGN KEY(current_version_id) REFERENCES itinerary_version (id) ON DELETE SET NULL ON UPDATE CASCADE;
