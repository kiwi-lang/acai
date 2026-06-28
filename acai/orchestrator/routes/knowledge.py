"""Knowledge document routes — CRUD, search, tags, facets, and vector/semantic search."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from acai.orchestrator.routes import RouterDeps

log = logging.getLogger(__name__)


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def create_knowledge_router(deps: RouterDeps) -> APIRouter:
    """Build the /knowledge/* router."""

    router = APIRouter(tags=["knowledge"])
    knowledge = deps.knowledge
    knowledge_db = deps.knowledge_db

    @router.get("/knowledge")
    def list_knowledge(request: Request):
        subject = request.query_params.get("subject", "")
        subsubject = request.query_params.get("subsubject", "")
        if not subject and not subsubject:
            return knowledge.tree()
        docs = knowledge.list(subject=subject, subsubject=subsubject)
        return [d.summary() for d in docs]

    @router.get("/knowledge/search")
    def search_knowledge(request: Request):
        query = request.query_params.get("q", "")
        subject = request.query_params.get("subject", "")
        subsubject = request.query_params.get("subsubject", "")
        mode = request.query_params.get("mode", "fts")
        limit = int(request.query_params.get("limit", "20"))
        if not query:
            return JSONResponse({"error": "q parameter is required"}, status_code=400)

        if mode == "vector" or mode == "semantic":
            return _vector_search(deps, query, limit, subject)

        if mode == "hybrid":
            return _hybrid_search(deps, query, limit, subject)

        if mode == "fts":
            fts_results = knowledge_db.fts_search(query, limit=limit)
            if fts_results:
                out = []
                for hit in fts_results:
                    doc = knowledge.get_by_path(hit["path"])
                    if doc:
                        if subject and doc.subject != subject:
                            continue
                        if subsubject and doc.subsubject != subsubject:
                            continue
                        d = doc.to_dict()
                        d["snippet"] = hit["snippet"]
                        d["rank"] = hit["rank"]
                        out.append(d)
                return out

        docs = knowledge.search(query, subject=subject, subsubject=subsubject)
        return [d.to_dict() for d in docs]

    @router.get("/knowledge/query")
    def query_knowledge(request: Request):
        params = dict(request.query_params)
        result = knowledge_db.query(
            subject=params.get("subject", ""),
            subsubject=params.get("subsubject", ""),
            tag=params.get("tag", ""),
            personality=params.get("personality", ""),
            matter=params.get("matter", ""),
            energy=params.get("energy", ""),
            space=params.get("space", ""),
            time=params.get("time", ""),
            limit=int(params.get("limit", "100")),
            offset=int(params.get("offset", "0")),
        )
        return result

    @router.get("/knowledge/tags")
    def list_knowledge_tags():
        return knowledge_db.list_tags()

    @router.get("/knowledge/facets/{facet}")
    def list_knowledge_facet_values(facet: str):
        try:
            return knowledge_db.list_facet_values(facet)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/knowledge", status_code=201)
    async def create_knowledge(request: Request):
        data = await _json_body(request)
        subject = data.get("subject", "")
        subsubject = data.get("subsubject", "")
        title = data.get("title", "")
        if not subject or not subsubject or not title:
            return JSONResponse(
                {"error": "subject, subsubject, and title are required"},
                status_code=400,
            )
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        facets_raw = data.get("facets") or {}
        if not isinstance(facets_raw, dict):
            facets_raw = {}

        doc = knowledge.create(
            subject=subject,
            subsubject=subsubject,
            title=title,
            content=data.get("content", ""),
            tags=tags,
        )
        try:
            knowledge_db.upsert(
                doc.subject, doc.subsubject, doc.title,
                tags=tags, facets=facets_raw, updated_at=doc.updated_at,
                content=doc.content,
            )
        except Exception:
            log.exception("knowledge_db.upsert failed for %s", doc.path)
        _auto_index_vector(deps, doc)
        doc.tags = tags
        from acai.knowledge import Facets
        doc.facets = Facets.from_dict(facets_raw)
        return doc.to_dict()

    @router.get("/knowledge/{subject}/{subsubject}/{title}")
    def get_knowledge(subject: str, subsubject: str, title: str):
        doc = knowledge.get(subject, subsubject, title)
        if doc is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        meta = knowledge_db.get(doc.path)
        if meta:
            doc.tags = meta["tags"]
            from acai.knowledge import Facets
            doc.facets = Facets.from_dict(meta["facets"])
        return doc.to_dict()

    @router.patch("/knowledge/{subject}/{subsubject}/{title}")
    async def update_knowledge(subject: str, subsubject: str, title: str, request: Request):
        data = await _json_body(request)
        content = data.get("content", "")
        if not content:
            return JSONResponse({"error": "content is required"}, status_code=400)
        doc = knowledge.update(subject, subsubject, title, content)
        if doc is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        tags = data.get("tags")
        facets = data.get("facets")
        if not isinstance(tags, list):
            tags = None
        if not isinstance(facets, dict):
            facets = None
        existing_meta = knowledge_db.get(doc.path) or {}
        try:
            knowledge_db.upsert(
                doc.subject, doc.subsubject, doc.title,
                tags=tags if tags is not None else existing_meta.get("tags", []),
                facets=facets if facets is not None else existing_meta.get("facets", {}),
                updated_at=doc.updated_at,
                content=doc.content,
            )
        except Exception:
            log.exception("knowledge_db.upsert failed for %s", doc.path)
        _auto_index_vector(deps, doc)
        return doc.to_dict()

    @router.post("/knowledge/{subject}/{subsubject}/{title}/append")
    async def append_knowledge(subject: str, subsubject: str, title: str, request: Request):
        data = await _json_body(request)
        content = data.get("content", "")
        if not content:
            return JSONResponse({"error": "content is required"}, status_code=400)
        doc = knowledge.append_content(subject, subsubject, title, content)
        if doc is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            existing_meta = knowledge_db.get(doc.path) or {}
            knowledge_db.upsert(
                doc.subject, doc.subsubject, doc.title,
                tags=existing_meta.get("tags", []),
                facets=existing_meta.get("facets", {}),
                updated_at=doc.updated_at,
                content=doc.content,
            )
        except Exception:
            log.exception("knowledge_db.upsert (append) failed for %s", doc.path)
        _auto_index_vector(deps, doc)
        return doc.to_dict()

    @router.post("/knowledge/{subject}/{subsubject}/{title}/delete")
    async def delete_knowledge(subject: str, subsubject: str, title: str):
        deleted = knowledge.delete(subject, subsubject, title)
        if not deleted:
            return JSONResponse({"error": "not found"}, status_code=404)
        from acai.knowledge import slugify
        knowledge_db.remove(f"{slugify(subject)}/{slugify(subsubject)}/{slugify(title)}")
        return {"deleted": True}

    # ------------------------------------------------------------------
    # Vector / semantic search endpoints
    # ------------------------------------------------------------------

    @router.post("/knowledge/vectors/sync")
    async def sync_vectors():
        """Re-index all knowledge documents into the vector store."""
        vs = _get_vector_store(deps)
        if vs is None:
            return JSONResponse(
                {"error": "embedding endpoint not configured or unreachable"},
                status_code=503,
            )
        result = vs.sync(knowledge)
        return result

    @router.get("/knowledge/vectors/stats")
    def vector_stats():
        """Return vector index statistics."""
        vs = _get_vector_store(deps)
        if vs is None:
            return {"total_chunks": 0, "total_documents": 0, "embedding_available": False}
        return vs.stats()

    @router.post("/knowledge/vectors/index")
    async def index_single_document(request: Request):
        """Index (or re-index) a single document by path."""
        data = await _json_body(request)
        path = data.get("path", "")
        if not path:
            return JSONResponse({"error": "path is required"}, status_code=400)

        doc = knowledge.get_by_path(path)
        if not doc:
            return JSONResponse({"error": f"document not found: {path}"}, status_code=404)

        vs = _get_vector_store(deps)
        if vs is None:
            return JSONResponse(
                {"error": "embedding endpoint not configured or unreachable"},
                status_code=503,
            )
        try:
            count = vs.index_document(doc.path, doc.content, metadata={
                "subject": doc.subject,
                "subsubject": doc.subsubject,
                "title": doc.title,
                "tags": doc.tags,
            })
            return {"path": doc.path, "chunks_indexed": count}
        except Exception as exc:
            log.exception("vector index failed for %s", path)
            return JSONResponse({"error": str(exc)}, status_code=500)

    return router


def _get_vector_store(deps: RouterDeps):
    """Lazily construct a VectorStore from deps config."""
    from acai.knowledge.vectors import VectorStore

    knowledge_dir = os.path.join(deps.config.workspace, "knowledge")
    emb_cfg = getattr(deps.config, "embedding", None)
    endpoint = (
        (emb_cfg.endpoint if emb_cfg else "")
        or os.environ.get("ACAI_EMBEDDING_ENDPOINT", "")
    )
    if not endpoint:
        return None

    model = (
        (emb_cfg.model if emb_cfg else "")
        or os.environ.get("ACAI_EMBEDDING_MODEL", "text-embedding")
    )
    chunk_size = emb_cfg.chunk_size if emb_cfg else 512
    chunk_overlap = emb_cfg.chunk_overlap if emb_cfg else 64

    vs = VectorStore(
        knowledge_dir,
        endpoint=endpoint,
        model=model,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    if not vs.embedding_available:
        return None
    return vs


def _vector_search(deps: RouterDeps, query: str, limit: int, path_filter: str = ""):
    """Pure vector similarity search."""
    vs = _get_vector_store(deps)
    if vs is None:
        return JSONResponse(
            {"error": "embedding endpoint not configured or unreachable"},
            status_code=503,
        )
    try:
        results = vs.search(query, limit=limit, path_filter=path_filter)
        return [
            {
                "path": hit.path,
                "chunk_index": hit.chunk_index,
                "chunk_text": hit.chunk_text,
                "score": hit.score,
                "metadata": hit.metadata,
            }
            for hit in results
        ]
    except Exception as exc:
        log.exception("vector search failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


def _hybrid_search(deps: RouterDeps, query: str, limit: int, path_filter: str = ""):
    """Hybrid search combining vector + FTS with reciprocal rank fusion."""
    vs = _get_vector_store(deps)
    if vs is None:
        # Fallback to FTS only
        fts_results = deps.knowledge_db.fts_search(query, limit=limit)
        docs = []
        for hit in fts_results:
            doc = deps.knowledge.get_by_path(hit["path"])
            if doc:
                d = doc.to_dict()
                d["snippet"] = hit["snippet"]
                d["rank"] = hit["rank"]
                docs.append(d)
        return docs

    fts_results = deps.knowledge_db.fts_search(query, limit=limit)
    try:
        results = vs.hybrid_search(query, fts_results, limit=limit)
        return [
            {
                "path": hit.path,
                "chunk_index": hit.chunk_index,
                "chunk_text": hit.chunk_text,
                "score": hit.score,
                "metadata": hit.metadata,
            }
            for hit in results
        ]
    except Exception as exc:
        log.exception("hybrid search failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


def _auto_index_vector(deps: RouterDeps, doc) -> None:
    """Best-effort background vector indexing when a doc is created/updated."""
    try:
        vs = _get_vector_store(deps)
        if vs is None:
            return
        vs.index_document(doc.path, doc.content, metadata={
            "subject": doc.subject,
            "subsubject": doc.subsubject,
            "title": doc.title,
            "tags": getattr(doc, "tags", []),
        })
    except Exception:
        log.debug("auto vector index failed for %s", doc.path, exc_info=True)
