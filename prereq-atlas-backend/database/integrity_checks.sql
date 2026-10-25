-- =============================================================================
-- 引用完整性巡检脚本（不使用数据库外键，由本脚本补偿检查）
--
-- 由 毕业设计方案/tools/build_db_design_assets.py 根据 schema.sql 的 @ref 声明生成，
-- 请勿手工修改。orphan_rows / bad_rows 为 0 表示通过；非 0 说明存在孤儿数据或
-- 业务一致性破坏，需要按 reference_name 定位并修复。
--
-- 执行：
--   mysql --default-character-set=utf8mb4 -u root -p prereq_atlas < database/integrity_checks.sql
-- =============================================================================

SELECT 'graph_version(base_version_id) -> graph_version(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_version` c
LEFT JOIN `graph_version` p ON p.`id` = c.`base_version_id`
WHERE c.`base_version_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'graph_version(created_by) -> app_user(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_version` c
LEFT JOIN `app_user` p ON p.`id` = c.`created_by`
WHERE c.`created_by` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'graph_version(published_by) -> app_user(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_version` c
LEFT JOIN `app_user` p ON p.`id` = c.`published_by`
WHERE c.`published_by` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'import_batch(operator_id) -> app_user(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `import_batch` c
LEFT JOIN `app_user` p ON p.`id` = c.`operator_id`
WHERE c.`operator_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'import_raw_node(batch_id) -> import_batch(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `import_raw_node` c
LEFT JOIN `import_batch` p ON p.`id` = c.`batch_id`
WHERE c.`batch_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'import_raw_node(mapped_concept_id) -> concept(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `import_raw_node` c
LEFT JOIN `concept` p ON p.`id` = c.`mapped_concept_id`
WHERE c.`mapped_concept_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'import_raw_edge(batch_id) -> import_batch(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `import_raw_edge` c
LEFT JOIN `import_batch` p ON p.`id` = c.`batch_id`
WHERE c.`batch_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'chapter_revision(version_id) -> graph_version(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `chapter_revision` c
LEFT JOIN `graph_version` p ON p.`id` = c.`version_id`
WHERE c.`version_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'concept_revision(version_id) -> graph_version(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `concept_revision` c
LEFT JOIN `graph_version` p ON p.`id` = c.`version_id`
WHERE c.`version_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'concept_revision(concept_id) -> concept(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `concept_revision` c
LEFT JOIN `concept` p ON p.`id` = c.`concept_id`
WHERE c.`concept_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'concept_revision(version_id, chapter_code) -> chapter_revision(version_id, chapter_code)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `concept_revision` c
LEFT JOIN `chapter_revision` p ON p.`version_id` = c.`version_id` AND p.`chapter_code` = c.`chapter_code`
WHERE c.`version_id` IS NOT NULL AND c.`chapter_code` IS NOT NULL AND p.`version_id` IS NULL
UNION ALL
SELECT 'resource_revision(version_id, concept_id) -> concept_revision(version_id, concept_id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `resource_revision` c
LEFT JOIN `concept_revision` p ON p.`version_id` = c.`version_id` AND p.`concept_id` = c.`concept_id`
WHERE c.`version_id` IS NOT NULL AND c.`concept_id` IS NOT NULL AND p.`version_id` IS NULL
UNION ALL
SELECT 'graph_relation(version_id) -> graph_version(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_relation` c
LEFT JOIN `graph_version` p ON p.`id` = c.`version_id`
WHERE c.`version_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'graph_relation(version_id, source_concept_id) -> concept_revision(version_id, concept_id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_relation` c
LEFT JOIN `concept_revision` p ON p.`version_id` = c.`version_id` AND p.`concept_id` = c.`source_concept_id`
WHERE c.`version_id` IS NOT NULL AND c.`source_concept_id` IS NOT NULL AND p.`version_id` IS NULL
UNION ALL
SELECT 'graph_relation(version_id, target_concept_id) -> concept_revision(version_id, concept_id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_relation` c
LEFT JOIN `concept_revision` p ON p.`version_id` = c.`version_id` AND p.`concept_id` = c.`target_concept_id`
WHERE c.`version_id` IS NOT NULL AND c.`target_concept_id` IS NOT NULL AND p.`version_id` IS NULL
UNION ALL
SELECT 'graph_relation(source_batch_id) -> import_batch(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_relation` c
LEFT JOIN `import_batch` p ON p.`id` = c.`source_batch_id`
WHERE c.`source_batch_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'graph_relation(reviewed_by) -> app_user(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_relation` c
LEFT JOIN `app_user` p ON p.`id` = c.`reviewed_by`
WHERE c.`reviewed_by` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'active_graph(version_id) -> graph_version(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `active_graph` c
LEFT JOIN `graph_version` p ON p.`id` = c.`version_id`
WHERE c.`version_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'active_graph(updated_by) -> app_user(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `active_graph` c
LEFT JOIN `app_user` p ON p.`id` = c.`updated_by`
WHERE c.`updated_by` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'graph_audit(version_id) -> graph_version(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_audit` c
LEFT JOIN `graph_version` p ON p.`id` = c.`version_id`
WHERE c.`version_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'graph_audit(operator_id) -> app_user(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `graph_audit` c
LEFT JOIN `app_user` p ON p.`id` = c.`operator_id`
WHERE c.`operator_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'user_mastery(user_id) -> app_user(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `user_mastery` c
LEFT JOIN `app_user` p ON p.`id` = c.`user_id`
WHERE c.`user_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'user_mastery(concept_id) -> concept(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `user_mastery` c
LEFT JOIN `concept` p ON p.`id` = c.`concept_id`
WHERE c.`concept_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'user_mastery(declared_version_id) -> graph_version(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `user_mastery` c
LEFT JOIN `graph_version` p ON p.`id` = c.`declared_version_id`
WHERE c.`declared_version_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'review_plan(owner_id) -> app_user(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `review_plan` c
LEFT JOIN `app_user` p ON p.`id` = c.`owner_id`
WHERE c.`owner_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'review_plan(graph_version_id) -> graph_version(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `review_plan` c
LEFT JOIN `graph_version` p ON p.`id` = c.`graph_version_id`
WHERE c.`graph_version_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'review_plan(based_on_plan_id) -> review_plan(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `review_plan` c
LEFT JOIN `review_plan` p ON p.`id` = c.`based_on_plan_id`
WHERE c.`based_on_plan_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'review_plan_node(plan_id) -> review_plan(id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `review_plan_node` c
LEFT JOIN `review_plan` p ON p.`id` = c.`plan_id`
WHERE c.`plan_id` IS NOT NULL AND p.`id` IS NULL
UNION ALL
SELECT 'review_plan_node(graph_version_id, concept_id) -> concept_revision(version_id, concept_id)' AS reference_name,
       COUNT(*) AS orphan_rows
FROM `review_plan_node` c
LEFT JOIN `concept_revision` p ON p.`version_id` = c.`graph_version_id` AND p.`concept_id` = c.`concept_id`
WHERE c.`graph_version_id` IS NOT NULL AND c.`concept_id` IS NOT NULL AND p.`version_id` IS NULL
ORDER BY reference_name;

-- -----------------------------------------------------------------------------
-- 业务一致性：不用外键无法表达的两条规则
-- -----------------------------------------------------------------------------

-- 1) 方案节点的版本必须等于所属方案的版本（不能把别版节点混入方案）
SELECT 'review_plan_node.graph_version_id = review_plan.graph_version_id' AS check_name,
       COUNT(*) AS bad_rows
FROM review_plan_node n
JOIN review_plan p ON p.id = n.plan_id
WHERE n.graph_version_id <> p.graph_version_id;

-- 2) 生效指针必须指向已发布版本
SELECT 'active_graph points to a PUBLISHED version' AS check_name,
       COUNT(*) AS bad_rows
FROM active_graph a
JOIN graph_version v ON v.id = a.version_id
WHERE v.state <> 'PUBLISHED';
