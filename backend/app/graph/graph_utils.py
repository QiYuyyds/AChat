"""graph_utils — Cypher 模板函数 + 确定性 ID 计算 + 实体名归一化。

集中管理所有 Neo4j 图操作的 Cypher 查询字符串，按 user_label 隔离。
所有 ID 计算函数确保跨 chunk / 跨文档的幂等 MERGE。
"""
from __future__ import annotations

import hashlib
import re

_TAG_RE = re.compile(r"[^A-Za-z0-9_]")


def normalize_entity_name(text: str) -> str:
    """归一化实体名：去首尾空白 + 小写化 + 压缩内部空白。"""
    if not text:
        return ""
    return " ".join(text.strip().lower().split())


def _hashstr(text: str, length: int = 32) -> str:
    """确定性 SHA-256 hash 截断。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def compute_entity_id(user_id: str, normalized_name: str, label: str) -> str:
    """计算 entity 确定性 ID：hashstr(user_id:normalized_name:label)。"""
    return _hashstr(f"{user_id}:{normalized_name}:{label}")


def compute_triple_id(
    user_id: str,
    source_name: str,
    source_label: str,
    relation_type: str,
    target_name: str,
    target_label: str,
) -> str:
    """计算 triple 确定性 ID：hashstr(user_id:source:source_label:relation:target:target_label)。"""
    return _hashstr(
        f"{user_id}:{source_name}:{source_label}:{relation_type}:{target_name}:{target_label}"
    )


def safe_user_label(user_id: str) -> str:
    """将 user_id hash 为合法 Neo4j 标签字符串（u_ + hash[:14]）。"""
    if not user_id:
        return "u_anonymous"
    return "u_" + _hashstr(user_id, length=14)


def cypher_merge_chunk(user_label: str) -> str:
    """MERGE Chunk 节点 + 元数据 SET。"""
    return (
        f"MERGE (c:Chunk:UserKG:`{user_label}` {{chunk_id: $chunk_id, doc_hash: $doc_hash}}) "
        "SET c.file_id = $file_id, c.pg_id = $pg_id, "
        "c.content_preview = $content_preview"
    )


def cypher_merge_entity_mention(user_label: str) -> str:
    """MATCH Chunk + MERGE Entity + MERGE MENTIONS 边。"""
    return (
        f"MATCH (c:Chunk:UserKG:`{user_label}` {{chunk_id: $chunk_id, doc_hash: $doc_hash}}) "
        f"MERGE (e:Entity:UserKG:`{user_label}` {{entity_id: $entity_id}}) "
        "SET e.name = $name, e.type = $type, e.label = $label, "
        "e.attributes = $attributes, e.doc_hash = $doc_hash "
        f"MERGE (c)-[m:MENTIONS {{chunk_id: $chunk_id, doc_hash: $doc_hash}}]->(e) "
        "SET m.file_id = $file_id"
    )


def cypher_merge_relation(user_label: str) -> str:
    """MATCH source/target Entity + MERGE RELATION 边。"""
    return (
        f"MATCH (a:Entity:UserKG:`{user_label}` {{entity_id: $source_id}}) "
        f"MATCH (b:Entity:UserKG:`{user_label}` {{entity_id: $target_id}}) "
        "MERGE (a)-[r:RELATION {triple_id: $triple_id}]->(b) "
        "SET r.relation_type = $relation_type, r.text = $text, "
        "r.chunk_id = $chunk_id, r.doc_hash = $doc_hash, r.file_id = $file_id, r.pg_id = $pg_id"
    )


def cypher_delete_document(user_label: str) -> str:
    """删 RELATION 边 → 删 MENTIONS 边 → orphan Entity 清理 → 删 Chunk 节点。"""
    return (
        f"MATCH (n:UserKG:`{user_label}`)-[r {{doc_hash: $doc_hash}}]-() "
        "DELETE r "
        "WITH 1 AS _ "
        f"MATCH (e:Entity:UserKG:`{user_label}`) "
        "WHERE e.doc_hash = $doc_hash AND NOT (e)--() "
        "DELETE e "
        "WITH 1 AS _ "
        f"MATCH (c:Chunk:UserKG:`{user_label}` {{doc_hash: $doc_hash}}) "
        "DELETE c"
    )


def cypher_query_entity_ids_by_doc_hash(user_label: str) -> str:
    """查 entity_id 列表用于 Milvus 清理。"""
    return (
        f"MATCH (e:Entity:UserKG:`{user_label}`) "
        "WHERE e.doc_hash = $doc_hash "
        "RETURN e.entity_id AS entity_id"
    )


def cypher_query_triple_ids_by_doc_hash(user_label: str) -> str:
    """查 triple_id 列表用于 Milvus 清理。"""
    return (
        f"MATCH (:Entity:UserKG:`{user_label}`)-[r:RELATION]->(:Entity:UserKG:`{user_label}`) "
        "WHERE r.doc_hash = $doc_hash "
        "RETURN r.triple_id AS triple_id"
    )


def cypher_query_entity_by_name(user_label: str) -> str:
    """按实体名查询 Entity 节点（用于 PPR 种子匹配）。"""
    return (
        f"MATCH (e:Entity:UserKG:`{user_label}`) "
        "WHERE e.name IN $names "
        "RETURN e.entity_id AS entity_id, e.name AS name, e.type AS type, e.label AS label"
    )


def cypher_search_direct(user_label: str) -> str:
    """直接匹配实体所在 chunk（APOC 不可用时的降级查询）。"""
    return (
        f"MATCH (c:Chunk:UserKG:`{user_label}`)-[:MENTIONS]->(e:Entity:UserKG:`{user_label}`) "
        "WHERE e.name IN $names "
        "RETURN c.pg_id AS pgid, e.name AS name "
        "ORDER BY pgid LIMIT $limit"
    )


def cypher_search_subgraph(user_label: str) -> str:
    """子图遍历查询（1~2 跳），通过 MENTIONS 边从实体反查 chunk。"""
    return (
        f"MATCH (e:Entity:UserKG:`{user_label}`) WHERE e.name IN $names "
        "CALL apoc.path.subgraphNodes(e, {"
        "  maxLevel: $hops,"
        '  relationshipFilter: "RELATION|MENTIONS"'
        "}) "
        "YIELD node AS neighbor "
        f"WHERE neighbor:Chunk:UserKG:`{user_label}` AND neighbor.pg_id IS NOT NULL "
        "WITH neighbor, collect(DISTINCT e.name) AS seeds, "
        "toInteger(apoc.node.degree(neighbor)) AS degree "
        "RETURN neighbor.pg_id AS pgid, seeds, degree, max(degree) AS deg "
        "ORDER BY size(seeds) DESC, deg DESC "
        "LIMIT $limit"
    )


def cypher_search_ppr_gds(user_label: str, has_weights: bool = False) -> str:
    """GDS pageRank.stream 查询（带 user_label 隔离）。"""
    if has_weights:
        return (
            f"MATCH (e:Entity:UserKG:`{user_label}`) WHERE e.name IN $names "
            "WITH collect(DISTINCT e) AS seedNodes "
            "UNWIND seedNodes AS seed "
            "WITH collect(DISTINCT seed) AS allSeeds, collect(DISTINCT id(seed)) AS seedIds "
            "WITH allSeeds, seedIds "
            "CALL gds.pageRank.stream('entityGraph', {"
            "  maxIterations: $maxIter,"
            "  dampingFactor: 0.85,"
            "  sourceNodes: allSeeds,"
            "  sourceNodeWeights: $seedWeights"
            "}) "
            "YIELD nodeId, score "
            "WITH gds.util.asNode(nodeId) AS neighbor, score "
            f"WHERE neighbor:Chunk:UserKG:`{user_label}` AND neighbor.pg_id IS NOT NULL "
            "RETURN neighbor.pg_id AS pgid, score, "
            "collect(DISTINCT seed.name) AS seeds "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )
    return (
        f"MATCH (e:Entity:UserKG:`{user_label}`) WHERE e.name IN $names "
        "WITH collect(DISTINCT e) AS seedNodes "
        "UNWIND seedNodes AS seed "
        "WITH seed, collect(DISTINCT seed) AS allSeeds "
        "CALL gds.pageRank.stream('entityGraph', {"
        "  maxIterations: $maxIter,"
        "  dampingFactor: 0.85,"
        "  sourceNodes: allSeeds"
        "}) "
        "YIELD nodeId, score "
        "WITH gds.util.asNode(nodeId) AS neighbor, score "
        f"WHERE neighbor:Chunk:UserKG:`{user_label}` AND neighbor.pg_id IS NOT NULL "
        "RETURN neighbor.pg_id AS pgid, score, "
        "collect(DISTINCT seed.name) AS seeds "
        "ORDER BY score DESC "
        "LIMIT $limit"
    )


def cypher_search_ppr_apoc(user_label: str) -> str:
    """APOC subgraphNodes 遍历 + 手动 PPR-style scoring（降级方案）。"""
    return (
        f"MATCH (e:Entity:UserKG:`{user_label}`) WHERE e.name IN $names "
        "CALL apoc.path.subgraphNodes(e, {"
        "  maxLevel: $depth,"
        '  relationshipFilter: "RELATION|MENTIONS"'
        "}) "
        "YIELD node AS neighbor "
        f"WHERE neighbor:Chunk:UserKG:`{user_label}` AND neighbor.pg_id IS NOT NULL "
        "WITH neighbor, collect(DISTINCT e.name) AS seeds, "
        "toInteger(apoc.node.degree(neighbor)) AS degree "
        "RETURN neighbor.pg_id AS pgid, seeds, degree "
        "LIMIT $limit"
    )


def cypher_graph_stats(user_label: str) -> str:
    """图谱统计：节点/边总数 + 实体类型分布（三条独立查询合并为一条 multi-statement）。"""
    return (
        f"MATCH (n:UserKG:`{user_label}`) "
        "WITH count(n) AS total_nodes "
        f"OPTIONAL MATCH (a:UserKG:`{user_label}`)-[r]->(b:UserKG:`{user_label}`) "
        "WITH total_nodes, count(r) AS total_edges "
        f"MATCH (e:Entity:UserKG:`{user_label}`) "
        "RETURN total_nodes, total_edges, "
        "collect(DISTINCT {type: coalesce(e.label, e.type, 'Unknown'), count: 0}) AS entity_types_raw"
    )


def cypher_graph_stats_nodes(user_label: str) -> str:
    """统计用户图分区中的节点总数。"""
    return (
        f"MATCH (n:UserKG:`{user_label}`) "
        "RETURN count(n) AS count"
    )


def cypher_graph_stats_edges(user_label: str) -> str:
    """统计用户图分区中的边总数。"""
    return (
        f"MATCH (:UserKG:`{user_label}`)-[r]->(:UserKG:`{user_label}`) "
        "RETURN count(r) AS count"
    )


def cypher_graph_stats_entity_types(user_label: str) -> str:
    """实体类型分布：按 label 字段分组计数。"""
    return (
        f"MATCH (e:Entity:UserKG:`{user_label}`) "
        "RETURN coalesce(e.label, e.type, 'Unknown') AS entity_label, count(*) AS count "
        "ORDER BY count DESC"
    )


def cypher_graph_subgraph(user_label: str, max_depth: int, exclude_chunk: bool) -> str:
    """子图查询：关键词过滤 + N 跳遍历，返回 nodes + edges。"""
    chunk_filter = " AND NOT n:Chunk" if exclude_chunk else ""
    depth = max(1, min(int(max_depth), 5))
    return (
        f"MATCH (n:UserKG:`{user_label}`) "
        "WHERE (toLower(n.name) CONTAINS toLower($keyword) OR $keyword = '*')"
        f"{chunk_filter} "
        "WITH n LIMIT $limit "
        "WITH collect(n) AS seeds "
        "UNWIND seeds AS seed "
        f"OPTIONAL MATCH p = (seed)-[*1..{depth}]-(m:UserKG:`{user_label}`) "
        "WITH seeds, p LIMIT $path_limit "
        "WITH seeds, collect(p) AS paths "
        "RETURN reduce(path_nodes = [], path IN paths | path_nodes + nodes(path)) + seeds AS nodes, "
        "reduce(path_edges = [], path IN paths | path_edges + relationships(path)) AS edges"
    )


def cypher_graph_labels(user_label: str) -> str:
    """查询用户图分区中所有去重的实体标签（排除 Entity / Chunk / UserKG）。"""
    return (
        f"MATCH (n:Entity:UserKG:`{user_label}`) "
        "UNWIND labels(n) AS node_label "
        "WITH DISTINCT node_label "
        "WHERE node_label <> 'Entity' AND node_label <> 'Chunk' AND node_label <> 'UserKG' "
        f"AND NOT node_label STARTS WITH 'u_' "
        "RETURN node_label AS label "
        "ORDER BY label"
    )
