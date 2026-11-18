"""知识库接口：文档上传、切片、检索（单库最小闭环）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFound
from ..kb import index_document, retrieve
from ..models import KbChunk, KbDoc, now
from ..runner import uid
from ..schemas import KbDocCreate, RetrieveRequest, kb_chunk_out, kb_doc_out

router = APIRouter()


@router.get("/kb/documents")
def list_documents(db: Session = Depends(get_db)):
    rows = db.query(KbDoc).order_by(KbDoc.created_at.desc()).all()
    return [kb_doc_out(d) for d in rows]


@router.post("/kb/documents", status_code=201)
def create_document(body: KbDocCreate, db: Session = Depends(get_db)):
    doc = KbDoc(id=uid("doc"), name=body.name, kind=body.kind, note=body.note,
                status="解析中", content=body.content)
    db.add(doc)
    db.commit()
    index_document(db, doc)
    return kb_doc_out(doc)


@router.get("/kb/documents/{doc_id}/chunks")
def list_chunks(doc_id: str, db: Session = Depends(get_db)):
    doc = db.get(KbDoc, doc_id)
    if not doc:
        raise NotFound("文档不存在")
    rows = db.query(KbChunk).filter(KbChunk.doc_id == doc_id).order_by(KbChunk.idx).all()
    return [kb_chunk_out(c) for c in rows]


@router.post("/kb/documents/{doc_id}/reindex")
def reindex_document(doc_id: str, db: Session = Depends(get_db)):
    doc = db.get(KbDoc, doc_id)
    if not doc:
        raise NotFound("文档不存在")
    count = index_document(db, doc)
    doc.updated_at = now()
    db.commit()
    return {"ok": True, "chunks": count}


@router.delete("/kb/documents/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    doc = db.get(KbDoc, doc_id)
    if not doc:
        raise NotFound("文档不存在")
    db.query(KbChunk).filter(KbChunk.doc_id == doc_id).delete()
    db.delete(doc)
    db.commit()
    return {"ok": True}


@router.post("/kb/retrieve")
def retrieve_chunks(body: RetrieveRequest, db: Session = Depends(get_db)):
    rows = retrieve(db, body.q, body.top_k)
    return {"q": body.q, "top_k": body.top_k, "rows": rows}


@router.get("/kb/info")
def kb_info(db: Session = Depends(get_db)):
    docs = db.query(KbDoc).count()
    chunks = db.query(KbChunk).count()
    return {"name": "业务口径库", "embedding": "hash-bigram-64", "docs": docs,
            "chunks": chunks, "mode": "单库 / 单进程 / 无 ACL"}
