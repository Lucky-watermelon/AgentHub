"""知识库接口：文档上传、编辑、切片、检索（单库最小闭环）。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import ApiError, NotFound
from ..kb import index_document, retrieve
from ..models import KbChunk, KbDoc, now
from ..runner import uid
from ..schemas import KbDocCreate, KbDocUpdate, RetrieveRequest, kb_chunk_out, kb_doc_out

router = APIRouter()


def _get_doc(db: Session, doc_id: str) -> KbDoc:
    doc = db.get(KbDoc, doc_id)
    if not doc:
        raise NotFound("文档不存在")
    return doc


@router.get("/kb/documents")
def list_documents(kind: str | None = None, q: str | None = None,
                   db: Session = Depends(get_db)):
    query = db.query(KbDoc)
    if kind and kind != "全部":
        query = query.filter(KbDoc.kind == kind)
    if q:
        query = query.filter(KbDoc.name.contains(q))
    return [kb_doc_out(d) for d in query.order_by(KbDoc.created_at.desc()).all()]


@router.get("/kb/documents/{doc_id}")
def get_document(doc_id: str, db: Session = Depends(get_db)):
    """文档详情：编辑弹窗需要拿到完整正文。"""
    doc = _get_doc(db, doc_id)
    data = kb_doc_out(doc)
    data["content"] = doc.content or ""
    return data


@router.post("/kb/documents", status_code=201)
def create_document(body: KbDocCreate, db: Session = Depends(get_db)):
    if not (body.content or "").strip():
        raise ApiError("文档正文不能为空", code="AH-KB-422", status_code=422)
    doc = KbDoc(id=uid("doc"), name=body.name, kind=body.kind, note=body.note,
                status="解析中", content=body.content)
    db.add(doc)
    db.commit()
    index_document(db, doc)
    return kb_doc_out(doc)


@router.patch("/kb/documents/{doc_id}")
def update_document(doc_id: str, body: KbDocUpdate, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    content_changed = body.content is not None and body.content != doc.content
    if body.name is not None:
        doc.name = body.name
    if body.kind is not None:
        doc.kind = body.kind
    if body.note is not None:
        doc.note = body.note
    if body.content is not None:
        if not body.content.strip():
            raise ApiError("文档正文不能为空", code="AH-KB-422", status_code=422)
        doc.content = body.content
    doc.updated_at = now()
    db.commit()
    if content_changed:
        index_document(db, doc)
    return kb_doc_out(doc)


@router.get("/kb/documents/{doc_id}/chunks")
def list_chunks(doc_id: str, db: Session = Depends(get_db)):
    _get_doc(db, doc_id)
    rows = db.query(KbChunk).filter(KbChunk.doc_id == doc_id).order_by(KbChunk.idx).all()
    return [kb_chunk_out(c) for c in rows]


@router.post("/kb/documents/{doc_id}/reindex")
def reindex_document(doc_id: str, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    count = index_document(db, doc)
    doc.updated_at = now()
    db.commit()
    return {"ok": True, "chunks": count}


@router.delete("/kb/documents/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    db.query(KbChunk).filter(KbChunk.doc_id == doc_id).delete()
    db.delete(doc)
    db.commit()
    return {"ok": True}


@router.post("/kb/retrieve")
def retrieve_chunks(body: RetrieveRequest, db: Session = Depends(get_db)):
    mode = "vector" if str(body.mode).lower() == "vector" else "hybrid"
    rows = retrieve(db, body.q, body.top_k, mode=mode)
    terms = [t for t in re.split(r"[\s,，、；;]+", body.q) if t]
    return {"q": body.q, "top_k": body.top_k, "mode": mode, "terms": terms, "rows": rows}


@router.get("/kb/info")
def kb_info(db: Session = Depends(get_db)):
    docs = db.query(KbDoc).all()
    chunks = db.query(KbChunk).count()
    kinds: dict[str, int] = {}
    for doc in docs:
        kinds[doc.kind] = kinds.get(doc.kind, 0) + 1
    return {"name": "业务口径库", "embedding": "hash-bigram-64", "docs": len(docs),
            "chunks": chunks, "kinds": kinds,
            "chars": sum(len(d.content or "") for d in docs),
            "mode": "单库 / 单进程 / 无 ACL"}
