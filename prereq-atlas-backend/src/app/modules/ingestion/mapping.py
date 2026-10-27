"""把原始 CSV 行规范化为候选记录。

规范化只重命名/裁剪字段，不推断学习阶段：``graph_level``、度数、根叶标记
等来源派生字段只留在原始值里供追溯。
"""

from __future__ import annotations

from typing import Any


def normalize_node(values: dict[str, str]) -> dict[str, str | None]:
    """节点原始行 → 概念候选；空字符串视为缺省。"""
    return {
        "source_key": values["node_id"],
        "label_zh": values["label_zh"] or None,
        "label_en": values["label_en"] or None,
        "chapter_code": values["module_id"] or None,
        "node_type": values["node_type"] or None,
    }


def normalize_edge(values: dict[str, str]) -> dict[str, Any]:
    """边原始行 → 关系候选；``strength`` 保留原值，不换算为权重。"""
    return {
        "source_key": values["edge_id"],
        "source_node_key": values["source_id"],
        "target_node_key": values["target_id"],
        "structural_type": values["edge_type"] or None,
        "source_strength": values["strength"] or None,
        "relation": values["relation"] or None,
    }
