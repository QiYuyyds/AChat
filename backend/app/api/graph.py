"""Graph visualization API — stats, subgraph query, labels.

All endpoints enforce user isolation via JWT cookie authentication.
Returns empty results (not errors) when Neo4j is unavailable.
"""

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import get_current_user
from app.db.models import User

router = APIRouter()


def _get_kg_store():
    """Lazy import to avoid circular dependency; returns the global KGStore or None."""
    from app.rag.graph_retrieval import GraphRetrieval
    return GraphRetrieval._kg_store


@router.get("/stats")
async def get_graph_stats(user: User = Depends(get_current_user)) -> dict:
    """返回用户图谱统计：节点/边总数 + 实体类型分布。"""
    kg_store = _get_kg_store()
    if kg_store is None:
        return {"total_nodes": 0, "total_edges": 0, "entity_types": []}
    kg_store.set_user_id(user.id)
    return await kg_store.get_stats()


@router.get("/subgraph")
async def get_graph_subgraph(
    user: User = Depends(get_current_user),
    keyword: str = Query(default="*", description="Search keyword to filter nodes by name"),
    max_depth: int = Query(default=2, ge=1, le=5, description="Maximum traversal depth"),
    max_nodes: int = Query(default=50, ge=1, le=200, description="Maximum number of nodes"),
    exclude_chunk: bool = Query(default=False, description="Exclude Chunk nodes"),
) -> dict:
    """返回用户图谱子图：关键词过滤 + N 跳遍历。"""
    kg_store = _get_kg_store()
    if kg_store is None:
        return {"nodes": [], "edges": []}
    kg_store.set_user_id(user.id)
    return await kg_store.query_subgraph(
        keyword=keyword,
        max_depth=max_depth,
        max_nodes=max_nodes,
        exclude_chunk=exclude_chunk,
    )


@router.get("/labels")
async def get_graph_labels(user: User = Depends(get_current_user)) -> dict:
    """返回用户图谱中所有去重的实体标签。"""
    kg_store = _get_kg_store()
    if kg_store is None:
        return {"labels": []}
    kg_store.set_user_id(user.id)
    labels = await kg_store.get_labels()
    return {"labels": labels}
