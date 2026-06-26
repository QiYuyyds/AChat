"""Memory and RAG tools — rag_search, rag_ingest, rag_list_documents, rag_delete_document, memory_recall.

Tools registered for the AGI-memory capability enhancement.
rag_ingest supports managed document lifecycle (title → DocumentService).
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok


async def rag_search_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Search the knowledge base using RAG hybrid search."""
    query = args.get("query", "").strip() if isinstance(args, dict) else str(args)
    if not query:
        return err("query is required for rag_search")

    try:
        from app.main import _rag_service  # type: ignore[attr-defined]
        if _rag_service is None:
            return err("RAG service not initialized")
        answer, results = await _rag_service.search(query)
        return ok({
            "answer": answer,
            "results": results[:5],  # Limit to top 5 for tool output
        })
    except Exception as e:
        return err(f"RAG search failed: {e}")


async def rag_ingest_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Ingest a document into the knowledge base.

    If ``title`` is provided, the document is created/updated via DocumentService
    (managed lifecycle with version tracking). Otherwise, falls back to direct
    RAG ingest without document management.
    """
    doc = args.get("document", "").strip() if isinstance(args, dict) else str(args)
    if not doc:
        return err("document is required for rag_ingest")

    title = args.get("title", "").strip() if isinstance(args, dict) else ""
    doc_type = args.get("doc_type", "note") if isinstance(args, dict) else "note"
    document_id = args.get("document_id", "").strip() if isinstance(args, dict) else ""

    try:
        if title:
            # Managed document lifecycle via DocumentService
            from app.main import _document_service  # type: ignore[attr-defined]
            if _document_service is None:
                return err("Document service not initialized")
            result = await _document_service.write_document(
                document_id=document_id or None,
                title=title,
                doc_type=doc_type,
                source="agent_generated",
                created_by=ctx.agent_id,
                content_md=doc,
                ingest_to_rag=True,
            )
            ingest_info = result.get("ingest") or {}
            chunk_count = ingest_info.get("chunk_count", 0)
            return ok({
                "chunk_count": chunk_count,
                "document_id": result["document"]["id"],
                "version_id": result["version"]["id"],
                "version": result["version"]["version"],
                "created": result["created"],
                "message": f"Document '{title}' {'created' if result['created'] else 'updated'} as version {result['version']['version']}, indexed into {chunk_count} chunks",
            })
        else:
            # Direct RAG ingest without document management
            from app.main import _rag_service  # type: ignore[attr-defined]
            if _rag_service is None:
                return err("RAG service not initialized")
            chunk_count = await _rag_service.ingest(doc)
            return ok({"chunk_count": chunk_count, "message": f"Document indexed into {chunk_count} chunks"})
    except Exception as e:
        return err(f"RAG ingest failed: {e}")


async def rag_list_documents_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """List all active documents in the knowledge base."""
    try:
        from app.main import _document_service  # type: ignore[attr-defined]
        if _document_service is None:
            return err("Document service not initialized")
        documents = await _document_service.list_documents()
        items = [
            {
                "id": d["id"],
                "title": d["title"],
                "doc_type": d["doc_type"],
                "source": d["source"],
                "latest_version": d["latest_version"],
                "updated_at": d["updated_at"],
                "latest_content_chars": d.get("latest_content_chars"),
                "latest_parser": d.get("latest_parser"),
            }
            for d in documents
        ]
        return ok({"documents": items, "count": len(items)})
    except Exception as e:
        return err(f"List documents failed: {e}")


async def rag_delete_document_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Delete a document from the knowledge base (soft-delete + RAG cleanup)."""
    document_id = args.get("document_id", "").strip() if isinstance(args, dict) else ""
    if not document_id:
        return err("document_id is required for rag_delete_document")

    try:
        from app.main import _document_service  # type: ignore[attr-defined]
        if _document_service is None:
            return err("Document service not initialized")
        deleted_chunks = await _document_service.delete_document(document_id)
        return ok({
            "document_id": document_id,
            "deleted_chunks": deleted_chunks,
            "message": f"Document deleted, {deleted_chunks} RAG chunks cleaned up",
        })
    except Exception as e:
        return err(f"Delete document failed: {e}")


async def memory_recall_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Recall relevant memories from long-term memory."""
    query = args.get("query", "").strip() if isinstance(args, dict) else str(args)
    if not query:
        return err("query is required for memory_recall")

    top_k = args.get("top_k", 3) if isinstance(args, dict) else 3

    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
        if _memory_service is None:
            return err("Memory service not initialized")
        items = await _memory_service.recall(query, top_k=top_k)
        memories = [
            {
                "content": item.content,
                "importance": item.importance,
                "score": item.score,
                "category": item.category,
            }
            for item in items
        ]
        # Also get preference context
        pref_context = _memory_service.get_preference_context()
        return ok({"memories": memories, "preferences": pref_context})
    except Exception as e:
        return err(f"Memory recall failed: {e}")


rag_search_tool = ToolDef(
    name="rag_search",
    description="Search the knowledge base using hybrid retrieval (semantic + keyword + graph). "
                "Returns the most relevant passages with answers.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to find relevant content in the knowledge base.",
            },
        },
        "required": ["query"],
    },
    handler=rag_search_handler,
)


rag_ingest_tool = ToolDef(
    name="rag_ingest",
    description="Ingest a document into the knowledge base for future retrieval. "
                "The document is split into chunks, embedded, and indexed. "
                "If title is provided, a managed document with version tracking is created via DocumentService.",
    parameters={
        "type": "object",
        "properties": {
            "document": {
                "type": "string",
                "description": "The document content to ingest into the knowledge base.",
            },
            "title": {
                "type": "string",
                "description": "Optional title. If provided, a managed document is created via DocumentService "
                               "with version tracking and RAG ingestion. If omitted, direct RAG ingest without document management.",
            },
            "doc_type": {
                "type": "string",
                "description": "Document type when creating a managed document (default: 'note').",
            },
            "document_id": {
                "type": "string",
                "description": "Optional existing document ID to update (creates a new version). "
                               "If omitted, a new document is created.",
            },
        },
        "required": ["document"],
    },
    handler=rag_ingest_handler,
)


rag_list_documents_tool = ToolDef(
    name="rag_list_documents",
    description="List all active documents in the knowledge base. "
                "Returns document metadata including title, type, version count, and update time.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=rag_list_documents_handler,
)


rag_delete_document_tool = ToolDef(
    name="rag_delete_document",
    description="Delete a document from the knowledge base. "
                "Soft-deletes the document and cleans up all RAG chunks (PG/ES/Milvus/Neo4j).",
    parameters={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The ID of the document to delete.",
            },
        },
        "required": ["document_id"],
    },
    handler=rag_delete_document_handler,
)


memory_recall_tool = ToolDef(
    name="memory_recall",
    description="Recall relevant long-term memories and user preferences. "
                "Use this to remember past conversations and user preferences.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The query to search for in long-term memory.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of memories to return (default: 3).",
            },
        },
        "required": ["query"],
    },
    handler=memory_recall_handler,
)
