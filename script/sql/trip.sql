-- 旅行主表
CREATE TABLE `trip` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT
        COMMENT '旅行编号',

    `request_json` JSON NOT NULL
        COMMENT '用户旅行需求',

    `current_version_id` BIGINT UNSIGNED NULL
        COMMENT '当前使用的行程版本编号',

    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        COMMENT '创建时间',

    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
        COMMENT '修改时间',

    PRIMARY KEY (`id`),

    KEY `idx_trip_current_version_id` (`current_version_id`)

) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '旅行主表';


-- 行程版本表
CREATE TABLE `itinerary_version` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT
        COMMENT '行程版本编号',

    `trip_id` BIGINT UNSIGNED NOT NULL
        COMMENT '所属旅行编号',

    `version_no` INT UNSIGNED NOT NULL
        COMMENT '版本号，例如 1、2、3',

    `request_snapshot_json` JSON NOT NULL
        COMMENT '生成该版本时的需求快照',

    `itinerary_json` JSON NOT NULL
        COMMENT '结构化行程内容',

    `validation_json` JSON NULL
        COMMENT '行程校验结果',

    `total_cost` DECIMAL(12, 2) NULL
        COMMENT '预计总花费',

    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        COMMENT '版本创建时间',

    PRIMARY KEY (`id`),

    UNIQUE KEY `uk_itinerary_version_trip_version`
        (`trip_id`, `version_no`),

    KEY `idx_itinerary_version_trip_id`
        (`trip_id`),

    CONSTRAINT `fk_itinerary_version_trip`
        FOREIGN KEY (`trip_id`)
        REFERENCES `trip` (`id`)
        ON DELETE CASCADE
        ON UPDATE CASCADE

) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '行程版本表';


-- 建立旅行与当前行程版本之间的关联
ALTER TABLE `trip`
    ADD CONSTRAINT `fk_trip_current_version`
        FOREIGN KEY (`current_version_id`)
        REFERENCES `itinerary_version` (`id`)
        ON DELETE SET NULL
        ON UPDATE CASCADE;