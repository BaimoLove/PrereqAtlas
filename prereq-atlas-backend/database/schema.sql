-- =============================================================================
-- 面向数据挖掘课程的先修知识图谱与复习路径规划系统
-- 数据库建表脚本
--
-- 版本    : 1.1
-- 日期    : 2026-10-24
-- 目标库  : MySQL 8.0.16 及以上（使用 CHECK 约束），InnoDB，utf8mb4
-- 对应文档: ../../毕业设计方案/数据库表设计.md
--
-- 约定
--   1. 表名、字段名使用小写蛇形命名；表名用单数。
--   2. 主键统一 BIGINT UNSIGNED AUTO_INCREMENT，联合主键用于“版本内从属”表。
--   3. 时间使用 DATETIME(3)，默认值由数据库给出，保留毫秒以支持并发排序。
--   4. 状态类字段使用 VARCHAR + CHECK，取值域写在字段注释与 CHECK 中，
--      便于后续增加取值而不重排 ENUM 序号。
--   5. 不使用 FOREIGN KEY 约束（项目硬性约定）。引用完整性由应用层在事务中
--      校验：写入前确认被引用行存在，删除前先处理子表，跨版本引用显式比对
--      版本号。每个引用列都建了索引，供连接、删除检查和孤儿数据巡检使用。
--      各表上方的 “@ref” 行声明逻辑引用，供 tools/build_db_design_assets.py
--      校验并生成 database/integrity_checks.sql，不改变表的执行语义。
--   6. 本脚本面向空库执行，不含 DROP TABLE，重复执行会因对象已存在而报错，
--      以避免误删已有数据。重建请使用独立的测试库。
--
-- 执行
--   mysql --default-character-set=utf8mb4 -u <管理员账号> -p < database/schema.sql
-- =============================================================================

CREATE DATABASE IF NOT EXISTS `prereq_atlas`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `prereq_atlas`;

SET NAMES utf8mb4;

-- =============================================================================
-- M01 账号与权限
-- =============================================================================

-- 账号表。管理员与学生共用一张表，以 role 区分；注册接口只创建 STUDENT。
-- 不保存明文口令，只保存 Argon2 散列；token_version 递增可作废旧令牌。
-- 本表不被其他表反向引用。
CREATE TABLE `app_user` (
  `id`                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户主键',
  `username`            VARCHAR(64)     NOT NULL                COMMENT '登录名，保留用户输入的原始大小写',
  `username_normalized` VARCHAR(64)     GENERATED ALWAYS AS (LOWER(TRIM(`username`))) STORED
                                                                COMMENT '规范化登录名：去首尾空格并转小写，由数据库生成',
  `password_hash`       VARCHAR(255)    NOT NULL                COMMENT '口令散列（Argon2），禁止存明文',
  `role`                VARCHAR(16)     NOT NULL DEFAULT 'STUDENT' COMMENT '角色：STUDENT 学生 / ADMIN 管理员',
  `is_active`           TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '账号是否启用：1 启用 / 0 停用',
  `token_version`       INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '令牌版本，递增后旧令牌失效',
  `last_login_at`       DATETIME(3)     NULL                    COMMENT '最近一次登录成功时间',
  `created_at`          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at`          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_username` (`username_normalized`),
  KEY `idx_user_role_active` (`role`, `is_active`),
  CONSTRAINT `ck_user_role`         CHECK (`role` IN ('STUDENT', 'ADMIN')),
  CONSTRAINT `ck_user_is_active`    CHECK (`is_active` IN (0, 1)),
  CONSTRAINT `ck_user_username_len` CHECK (CHAR_LENGTH(TRIM(`username`)) BETWEEN 1 AND 64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M01 账号：学生与管理员账号、角色、启用状态与令牌版本';

-- =============================================================================
-- M02 知识目录（稳定身份）
-- =============================================================================

-- 概念稳定身份。名称、简介等显示内容随版本变化，放在 concept_revision；
-- 掌握声明和方案引用这一层的 id，因此概念不会被“改名”变成另一个对象。
-- 来源命名空间与来源编号成对保存，避免把不同数据集的 N001 当成同一概念。
-- 本表不被其他表反向引用。
CREATE TABLE `concept` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '概念稳定身份主键',
  `code`             VARCHAR(64)     NOT NULL                COMMENT '系统内概念编码，全局唯一',
  `source_namespace` VARCHAR(64)     NULL                    COMMENT '来源命名空间，如 AL-CPL/data_mining',
  `source_key`       VARCHAR(64)     NULL                    COMMENT '来源节点编号，如 N001',
  `created_at`       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_concept_code` (`code`),
  UNIQUE KEY `uk_concept_source` (`source_namespace`, `source_key`),
  CONSTRAINT `ck_concept_source_pair` CHECK (
    (`source_namespace` IS NULL AND `source_key` IS NULL)
    OR (`source_namespace` IS NOT NULL AND `source_key` IS NOT NULL)
  ),
  CONSTRAINT `ck_concept_code_len` CHECK (CHAR_LENGTH(TRIM(`code`)) BETWEEN 1 AND 64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M02 概念稳定身份：跨版本不变的知识对象';

-- =============================================================================
-- M06 图谱管理（版本）
-- =============================================================================

-- 图谱版本。草稿可编辑，发布后冻结；version_label 是给人看的版本号。
-- 已发布版本不原地修改，后续编辑复制成新草稿（base_version_id 记录来源）。
-- 逻辑引用：
-- @ref base_version_id -> graph_version.id
-- @ref created_by -> app_user.id
-- @ref published_by -> app_user.id
CREATE TABLE `graph_version` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '版本主键',
  `version_label`      VARCHAR(64)     NOT NULL                COMMENT '版本标签，如 v1.0',
  `state`              VARCHAR(16)     NOT NULL DEFAULT 'DRAFT' COMMENT '状态：DRAFT 草稿 / PUBLISHED 已发布',
  `base_version_id`    BIGINT UNSIGNED NULL                    COMMENT '草稿的复制来源版本；空表示全新草稿；逻辑引用 graph_version.id',
  `draft_revision`     BIGINT UNSIGNED NOT NULL DEFAULT 0      COMMENT '草稿乐观锁版本号，每次内容写入递增',
  `content_hash`       CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
                                                               COMMENT '发布时图谱内容 SHA-256（十六进制）',
  `validated_revision` BIGINT UNSIGNED NULL                    COMMENT '最近一次校验通过时的 draft_revision',
  `note`               VARCHAR(255)    NULL                    COMMENT '版本说明',
  `created_by`         BIGINT UNSIGNED NOT NULL                COMMENT '创建人；逻辑引用 app_user.id',
  `created_at`         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `published_by`       BIGINT UNSIGNED NULL                    COMMENT '发布人；逻辑引用 app_user.id',
  `published_at`       DATETIME(3)     NULL                    COMMENT '发布时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_graph_version_label` (`version_label`),
  KEY `idx_graph_version_state` (`state`, `id`),
  KEY `idx_graph_version_base` (`base_version_id`),
  KEY `idx_graph_version_created_by` (`created_by`),
  KEY `idx_graph_version_published_by` (`published_by`),
  CONSTRAINT `ck_graph_version_state` CHECK (`state` IN ('DRAFT', 'PUBLISHED')),
  CONSTRAINT `ck_graph_version_published` CHECK (
    (`state` = 'DRAFT'     AND `published_at` IS NULL     AND `published_by` IS NULL     AND `content_hash` IS NULL)
    OR
    (`state` = 'PUBLISHED' AND `published_at` IS NOT NULL AND `published_by` IS NOT NULL AND `content_hash` IS NOT NULL)
  ),
  CONSTRAINT `ck_graph_version_validated` CHECK (
    `validated_revision` IS NULL OR `validated_revision` <= `draft_revision`
  ),
  CONSTRAINT `ck_graph_version_label_len` CHECK (CHAR_LENGTH(TRIM(`version_label`)) BETWEEN 1 AND 64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M06 图谱版本：草稿编辑、发布冻结与内容摘要';

-- =============================================================================
-- M08 数据导入（来源暂存）
-- =============================================================================

-- 导入批次。保存两个源文件的 SHA-256、来源说明与解析报告；
-- 暂存成功不等于发布，候选内容需经管理员接收进草稿、审核、校验后发布。
-- 逻辑引用：
-- @ref operator_id -> app_user.id
CREATE TABLE `import_batch` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '批次主键',
  `operator_id`  BIGINT UNSIGNED NOT NULL                COMMENT '导入操作人；逻辑引用 app_user.id',
  `source_note`  VARCHAR(255)    NULL                    COMMENT '来源、版本与许可说明',
  `node_sha256`  CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '节点文件 SHA-256',
  `edge_sha256`  CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '边文件 SHA-256',
  `status`       VARCHAR(16)     NOT NULL DEFAULT 'STAGED' COMMENT '状态：STAGED 已暂存 / FAILED 解析失败 / CONSUMED 已被草稿接收',
  `report_json`  JSON            NULL                    COMMENT '解析报告：错误、警告、计数与映射结果',
  `created_at`   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `finished_at`  DATETIME(3)     NULL                    COMMENT '解析完成时间',
  PRIMARY KEY (`id`),
  KEY `idx_import_batch_operator` (`operator_id`, `created_at`),
  KEY `idx_import_batch_status` (`status`, `id`),
  CONSTRAINT `ck_import_batch_status` CHECK (`status` IN ('STAGED', 'FAILED', 'CONSUMED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M08 导入批次：源文件摘要、来源说明与解析报告';

-- 导入原始节点。raw_json 原样保存来源字段，解析与映射结果单独成列，
-- 清洗不得覆盖原始值；批次内来源编号唯一。
-- 逻辑引用：
-- @ref batch_id -> import_batch.id
-- @ref mapped_concept_id -> concept.id
CREATE TABLE `import_raw_node` (
  `batch_id`          BIGINT UNSIGNED NOT NULL             COMMENT '所属批次；逻辑引用 import_batch.id',
  `source_node_key`   VARCHAR(64)     NOT NULL             COMMENT '来源节点编号，如 N001',
  `logical_record_no` INT UNSIGNED    NOT NULL             COMMENT '原始文件逻辑记录号（含表头，从 1 计）',
  `raw_json`          JSON            NOT NULL             COMMENT '原始字段值，未经清洗覆盖',
  `mapping_status`    VARCHAR(16)     NOT NULL DEFAULT 'UNMAPPED' COMMENT '映射状态：UNMAPPED/MAPPED/CONFLICT/IGNORED',
  `mapped_concept_id` BIGINT UNSIGNED NULL                 COMMENT '映射到的概念稳定身份；逻辑引用 concept.id',
  PRIMARY KEY (`batch_id`, `source_node_key`),
  UNIQUE KEY `uk_import_node_line` (`batch_id`, `logical_record_no`),
  KEY `idx_import_node_concept` (`mapped_concept_id`),
  CONSTRAINT `ck_import_node_mapping` CHECK (`mapping_status` IN ('UNMAPPED', 'MAPPED', 'CONFLICT', 'IGNORED')),
  CONSTRAINT `ck_import_node_mapped`  CHECK (`mapping_status` <> 'MAPPED' OR `mapped_concept_id` IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M08 导入原始节点：来源值、映射状态与逻辑记录号';

-- 导入原始边。方向为“先修 -> 后继”，与来源文件列序可能相反，转换时记录；
-- structural_type 与 strength 是来源结构分类和原值，不作为规划权重。
-- 逻辑引用：
-- @ref batch_id -> import_batch.id
CREATE TABLE `import_raw_edge` (
  `batch_id`          BIGINT UNSIGNED NOT NULL             COMMENT '所属批次；逻辑引用 import_batch.id',
  `source_edge_key`   VARCHAR(64)     NOT NULL             COMMENT '来源边编号，如 E001',
  `logical_record_no` INT UNSIGNED    NOT NULL             COMMENT '原始文件逻辑记录号（含表头，从 1 计）',
  `source_node_key`   VARCHAR(64)     NOT NULL             COMMENT '先修端来源节点编号',
  `target_node_key`   VARCHAR(64)     NOT NULL             COMMENT '后继端来源节点编号',
  `structural_type`   VARCHAR(16)     NULL                 COMMENT '来源结构分类：direct 直接 / transitive 传递推导',
  `source_strength`   DECIMAL(3,2)    NULL                 COMMENT '来源强度原值（1.00 / 0.60），不解释为必要性或权重',
  `raw_json`          JSON            NOT NULL             COMMENT '原始字段值，未经清洗覆盖',
  `review_note`       VARCHAR(500)    NULL                 COMMENT '人工核验备注',
  PRIMARY KEY (`batch_id`, `source_edge_key`),
  UNIQUE KEY `uk_import_edge_line` (`batch_id`, `logical_record_no`),
  KEY `idx_import_edge_source` (`batch_id`, `source_node_key`),
  KEY `idx_import_edge_target` (`batch_id`, `target_node_key`)
  -- 原始暂存表刻意不对 structural_type、strength、端点关系加 CHECK：
  -- 来源中的异常值应写入解析报告供人工核对，而不是让整批 INSERT 失败。
  -- 结构合法性与语义审核在 graph_relation 与发布校验中把关。
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M08 导入原始边：方向、来源结构分类与强度原值';

-- =============================================================================
-- M02 知识目录（版本内容）
-- =============================================================================

-- 章节版本。章节属于某一图谱版本，编码与来源教学模块编号对应，
-- display_order 表示来源建议的授课顺序，不是学生方案中的阶段。
-- 逻辑引用：
-- @ref version_id -> graph_version.id
CREATE TABLE `chapter_revision` (
  `version_id`    BIGINT UNSIGNED NOT NULL             COMMENT '所属图谱版本；逻辑引用 graph_version.id',
  `chapter_code`  VARCHAR(32)     NOT NULL             COMMENT '章节编码，如 M01',
  `name`          VARCHAR(100)    NOT NULL             COMMENT '章节名称',
  `display_order` INT UNSIGNED    NOT NULL DEFAULT 0   COMMENT '展示顺序（来源模块顺序）',
  PRIMARY KEY (`version_id`, `chapter_code`),
  CONSTRAINT `ck_chapter_revision_name`  CHECK (CHAR_LENGTH(TRIM(`name`)) > 0),
  CONSTRAINT `ck_chapter_revision_code`  CHECK (CHAR_LENGTH(TRIM(`chapter_code`)) BETWEEN 1 AND 32)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M02 章节版本：版本内的课程章节与展示顺序';

-- 概念版本。一个概念在某版本中的显示内容；联合主键 (version_id, concept_id)
-- 同时是 graph_relation、resource_revision、review_plan_node 的引用目标，
-- 应用层据此校验“跨版本混用节点”。
-- 逻辑引用：
-- @ref version_id -> graph_version.id
-- @ref concept_id -> concept.id
-- @ref version_id, chapter_code -> chapter_revision.version_id, chapter_code
CREATE TABLE `concept_revision` (
  `version_id`     BIGINT UNSIGNED NOT NULL             COMMENT '所属图谱版本；逻辑引用 graph_version.id',
  `concept_id`     BIGINT UNSIGNED NOT NULL             COMMENT '概念稳定身份；逻辑引用 concept.id',
  `chapter_code`   VARCHAR(32)     NULL                 COMMENT '所属章节编码，可为空表示未归章',
  `label_zh`       VARCHAR(200)    NOT NULL             COMMENT '中文名称',
  `label_en`       VARCHAR(200)    NULL                 COMMENT '英文名称',
  `node_type`      VARCHAR(32)     NULL                 COMMENT '概念类型：基础概念/方法/算法/模型/领域概念/评价指标/任务',
  `introduction`   TEXT            NULL                 COMMENT '简介',
  `learning_focus` TEXT            NULL                 COMMENT '学习重点',
  PRIMARY KEY (`version_id`, `concept_id`),
  KEY `idx_concept_revision_chapter` (`version_id`, `chapter_code`),
  KEY `idx_concept_revision_concept` (`concept_id`),
  KEY `idx_concept_revision_label` (`version_id`, `label_zh`),
  CONSTRAINT `ck_concept_revision_label` CHECK (CHAR_LENGTH(TRIM(`label_zh`)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M02 概念版本：版本内的名称、类型、简介与学习重点';

-- 参考资料。资料只属于某个概念版本，不跨版本共享；
-- resource_id 是版本内代理键，方案快照保存标题与链接副本，不引用本表。
-- 逻辑引用：
-- @ref version_id, concept_id -> concept_revision.version_id, concept_id
CREATE TABLE `resource_revision` (
  `resource_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '资料代理主键',
  `version_id`  BIGINT UNSIGNED NOT NULL                COMMENT '所属图谱版本',
  `concept_id`  BIGINT UNSIGNED NOT NULL                COMMENT '所属概念',
  `title`       VARCHAR(200)    NOT NULL                COMMENT '资料标题',
  `url`         VARCHAR(1000)   NOT NULL                COMMENT '资料链接，仅允许 http/https',
  `source`      VARCHAR(200)    NULL                    COMMENT '出处与许可说明',
  PRIMARY KEY (`resource_id`),
  KEY `idx_resource_revision_concept` (`version_id`, `concept_id`),
  CONSTRAINT `ck_resource_revision_title` CHECK (CHAR_LENGTH(TRIM(`title`)) > 0),
  CONSTRAINT `ck_resource_revision_url`   CHECK (LEFT(`url`, 7) = 'http://' OR LEFT(`url`, 8) = 'https://')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M02 参考资料：概念版本内的标题、链接与出处';

-- =============================================================================
-- M06 图谱管理（关系、生效指针、审计）
-- =============================================================================

-- 先修关系。方向固定为 source_concept_id（先修）-> target_concept_id（后继）。
-- necessity 是系统采用的依赖语义，structural_type 只是来源结构分类，两者不合并。
-- 同版本、同方向、同必要性的关系唯一；自依赖由 CHECK 拒绝；
-- REQUIRED 子图的无环性由应用在发布前校验（数据库不表达无环）。
-- 逻辑引用：
-- @ref version_id -> graph_version.id
-- @ref version_id, source_concept_id -> concept_revision.version_id, concept_id
-- @ref version_id, target_concept_id -> concept_revision.version_id, concept_id
-- @ref source_batch_id -> import_batch.id
-- @ref reviewed_by -> app_user.id
CREATE TABLE `graph_relation` (
  `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '关系主键',
  `version_id`        BIGINT UNSIGNED NOT NULL                COMMENT '所属图谱版本；逻辑引用 graph_version.id',
  `source_concept_id` BIGINT UNSIGNED NOT NULL                COMMENT '先修概念；与 version_id 一起逻辑引用 concept_revision',
  `target_concept_id` BIGINT UNSIGNED NOT NULL                COMMENT '后继概念；与 version_id 一起逻辑引用 concept_revision',
  `necessity`         VARCHAR(16)     NOT NULL DEFAULT 'REQUIRED' COMMENT '必要性：REQUIRED 强制 / RECOMMENDED 推荐 / OPTIONAL 可选关联',
  `structural_type`   VARCHAR(16)     NULL                    COMMENT '来源结构分类：direct / transitive，仅说明来源',
  `source_strength`   DECIMAL(3,2)    NULL                    COMMENT '来源强度原值，不作为规划权重',
  `review_status`     VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT '审核状态：PENDING / APPROVED / REJECTED',
  `rationale`         VARCHAR(500)    NULL                    COMMENT '纳入或审核理由',
  `source_batch_id`   BIGINT UNSIGNED NULL                    COMMENT '来源导入批次；逻辑引用 import_batch.id',
  `source_edge_key`   VARCHAR(64)     NULL                    COMMENT '来源边编号，如 E001',
  `reviewed_by`       BIGINT UNSIGNED NULL                    COMMENT '审核人；逻辑引用 app_user.id',
  `reviewed_at`       DATETIME(3)     NULL                    COMMENT '审核时间',
  `created_at`        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at`        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_graph_relation_edge` (`version_id`, `source_concept_id`, `target_concept_id`, `necessity`),
  KEY `idx_graph_relation_source` (`version_id`, `source_concept_id`),
  KEY `idx_graph_relation_target` (`version_id`, `target_concept_id`),
  KEY `idx_graph_relation_review` (`version_id`, `review_status`, `necessity`),
  KEY `idx_graph_relation_batch` (`source_batch_id`),
  KEY `idx_graph_relation_reviewer` (`reviewed_by`),
  CONSTRAINT `ck_graph_relation_no_self`   CHECK (`source_concept_id` <> `target_concept_id`),
  CONSTRAINT `ck_graph_relation_necessity` CHECK (`necessity` IN ('REQUIRED', 'RECOMMENDED', 'OPTIONAL')),
  CONSTRAINT `ck_graph_relation_structural` CHECK (`structural_type` IS NULL OR `structural_type` IN ('direct', 'transitive')),
  CONSTRAINT `ck_graph_relation_strength`  CHECK (`source_strength` IS NULL OR (`source_strength` >= 0 AND `source_strength` <= 1)),
  CONSTRAINT `ck_graph_relation_review`    CHECK (`review_status` IN ('PENDING', 'APPROVED', 'REJECTED')),
  CONSTRAINT `ck_graph_relation_reviewed`  CHECK (
    `review_status` = 'PENDING' OR (`reviewed_by` IS NOT NULL AND `reviewed_at` IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M06 先修关系：版本内先修到后继的依赖、必要性与审核记录';

-- 生效指针。固定单例行，指向当前对学生规划生效的已发布版本。
-- 应用层保证它只在发布事务中更新，且目标版本必须是 PUBLISHED。
-- 逻辑引用：
-- @ref version_id -> graph_version.id
-- @ref updated_by -> app_user.id
CREATE TABLE `active_graph` (
  `singleton_id` TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '单例主键，恒为 1',
  `version_id`   BIGINT UNSIGNED  NOT NULL           COMMENT '当前生效版本；逻辑引用 graph_version.id',
  `revision`     BIGINT UNSIGNED  NOT NULL DEFAULT 0 COMMENT '指针乐观锁版本号',
  `updated_by`   BIGINT UNSIGNED  NULL               COMMENT '最近更新人；逻辑引用 app_user.id',
  `updated_at`   DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`singleton_id`),
  KEY `idx_active_graph_version` (`version_id`),
  KEY `idx_active_graph_user` (`updated_by`),
  CONSTRAINT `ck_active_graph_singleton` CHECK (`singleton_id` = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M06 生效指针：单例记录当前发布版本，未发布时为空';

-- 操作审计。每次草稿创建、内容与关系修改、校验、发布都写一条；
-- 不修改历史行，按版本和时间追加查询。
-- 逻辑引用：
-- @ref version_id -> graph_version.id
-- @ref operator_id -> app_user.id
CREATE TABLE `graph_audit` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '审计主键',
  `version_id`      BIGINT UNSIGNED NOT NULL                COMMENT '操作的图谱版本；逻辑引用 graph_version.id',
  `operator_id`     BIGINT UNSIGNED NOT NULL                COMMENT '操作人；逻辑引用 app_user.id',
  `action`          VARCHAR(48)     NOT NULL                COMMENT '动作：CREATE_DRAFT/EDIT_CONTENT/EDIT_RELATION/IMPORT/VALIDATE/PUBLISH/DELETE_DRAFT',
  `revision_before` BIGINT UNSIGNED NULL                    COMMENT '操作前 draft_revision',
  `revision_after`  BIGINT UNSIGNED NULL                    COMMENT '操作后 draft_revision',
  `summary`         VARCHAR(500)    NULL                    COMMENT '操作摘要，不写入口令或令牌',
  `created_at`      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '操作时间',
  PRIMARY KEY (`id`),
  KEY `idx_graph_audit_version` (`version_id`, `created_at`, `id`),
  KEY `idx_graph_audit_operator` (`operator_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M06 图谱操作审计：草稿与发布全过程记录';

-- =============================================================================
-- M03 掌握声明
-- =============================================================================

-- 学生自评掌握。有记录即“已声明掌握”，无记录只表示未声明，不等于测评未通过。
-- 声明属于“学生-稳定概念”，不随方案变化；declared_version_id 记录声明时采用的版本。
-- 逻辑引用：
-- @ref user_id -> app_user.id
-- @ref concept_id -> concept.id
-- @ref declared_version_id -> graph_version.id
CREATE TABLE `user_mastery` (
  `user_id`             BIGINT UNSIGNED NOT NULL             COMMENT '学生用户；逻辑引用 app_user.id',
  `concept_id`          BIGINT UNSIGNED NOT NULL             COMMENT '概念稳定身份；逻辑引用 concept.id',
  `declared_version_id` BIGINT UNSIGNED NOT NULL             COMMENT '声明时使用的图谱版本；逻辑引用 graph_version.id',
  `declared_at`         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '声明时间',
  PRIMARY KEY (`user_id`, `concept_id`),
  KEY `idx_user_mastery_concept` (`concept_id`),
  KEY `idx_user_mastery_version` (`declared_version_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M03 掌握声明：学生对稳定概念的自评掌握';

-- =============================================================================
-- M05 方案与进度
-- =============================================================================

-- 复习方案。一次生成固定版本、目标、掌握声明集合与规则版本，结果以 JSON 快照
-- 保存；后续图谱或掌握状态变化不改写本行。request_id 是客户端幂等键，
-- (owner_id, request_id) 唯一。方案节点保存同一 graph_version_id，由应用层校验，
-- 使节点只能取自方案所固定的那个版本。
-- 逻辑引用：
-- @ref owner_id -> app_user.id
-- @ref graph_version_id -> graph_version.id
-- @ref based_on_plan_id -> review_plan.id
CREATE TABLE `review_plan` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '方案主键',
  `owner_id`         BIGINT UNSIGNED NOT NULL                COMMENT '方案所属学生；逻辑引用 app_user.id',
  `graph_version_id` BIGINT UNSIGNED NOT NULL                COMMENT '规划时固定的图谱版本；逻辑引用 graph_version.id',
  `title`            VARCHAR(120)    NOT NULL                COMMENT '方案标题',
  `request_id`       VARCHAR(64)     NOT NULL                COMMENT '客户端幂等请求键',
  `request_hash`     CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '规范化输入 SHA-256，用于同键异输入检测',
  `schema_version`   VARCHAR(16)     NOT NULL                COMMENT '快照结构版本',
  `algorithm_version` VARCHAR(32)    NOT NULL                COMMENT '规划规则版本',
  `input_snapshot`   JSON            NOT NULL                COMMENT '输入快照：目标、声明集合、版本与规则版本',
  `result_snapshot`  JSON            NOT NULL                COMMENT '结果快照：阶段、节点、解释、边界与结论',
  `based_on_plan_id` BIGINT UNSIGNED NULL                    COMMENT '重规划所依据的旧方案；逻辑引用 review_plan.id',
  `created_at`       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_review_plan_request` (`owner_id`, `request_id`),
  KEY `idx_review_plan_history` (`owner_id`, `created_at`, `id`),
  KEY `idx_review_plan_version` (`graph_version_id`),
  KEY `idx_review_plan_based_on` (`based_on_plan_id`),
  CONSTRAINT `ck_review_plan_title` CHECK (CHAR_LENGTH(TRIM(`title`)) BETWEEN 1 AND 120)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M05 复习方案：固定输入、结果快照与幂等键';

-- 方案节点。只保存“待复习节点”的阶段与可变进度；已掌握目标和掌握边界只存在于
-- 结果快照中，不生成可勾选行。计划删除时由应用先删节点再删方案（不设级联），
-- 节点的 graph_version_id 必须等于所属方案的 graph_version_id（应用层校验）。
-- 逻辑引用：
-- @ref plan_id -> review_plan.id
-- @ref graph_version_id, concept_id -> concept_revision.version_id, concept_id
CREATE TABLE `review_plan_node` (
  `plan_id`           BIGINT UNSIGNED NOT NULL             COMMENT '所属方案；逻辑引用 review_plan.id',
  `graph_version_id`  BIGINT UNSIGNED NOT NULL             COMMENT '方案固定版本，必须与方案一致（应用层校验）',
  `concept_id`        BIGINT UNSIGNED NOT NULL             COMMENT '待复习概念',
  `stage_index`       SMALLINT UNSIGNED NOT NULL           COMMENT '复习阶段序号，从 1 开始',
  `progress_status`   VARCHAR(16)     NOT NULL DEFAULT 'NOT_STARTED' COMMENT '进度：NOT_STARTED / IN_PROGRESS / COMPLETED',
  `progress_revision` INT UNSIGNED    NOT NULL DEFAULT 0   COMMENT '进度乐观锁版本号',
  `updated_at`        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`plan_id`, `concept_id`),
  KEY `idx_plan_node_stage` (`plan_id`, `stage_index`),
  KEY `idx_plan_node_concept` (`graph_version_id`, `concept_id`),
  CONSTRAINT `ck_plan_node_stage`  CHECK (`stage_index` >= 1),
  CONSTRAINT `ck_plan_node_status` CHECK (`progress_status` IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='M05 方案节点：待复习节点的阶段与方案内进度';

-- =============================================================================
-- 结束
-- =============================================================================
