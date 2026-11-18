"""知识库：文本切分、轻量向量化、检索。

裁剪后只保留单库最小闭环：无多租户 ACL、无密级。
向量用字符 bigram 哈希到 64 维，离线可跑；后续可替换为真实 embedding。
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from sqlalchemy.orm import Session

from .models import KbChunk, KbDoc

DIM = 64


def split_text(text: str, size: int = 500) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"\n{2,}|(?<=[。！？；])", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) <= size:
            buf += part
        else:
            if buf:
                chunks.append(buf)
            buf = part if len(part) <= size else part[:size]
    if buf:
        chunks.append(buf)
    return chunks or [text[:size]]


def vectorize(text: str) -> list[float]:
    vec = [0.0] * DIM
    clean = re.sub(r"\s+", "", text or "")
    for i in range(len(clean) - 1):
        gram = clean[i:i + 2]
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        vec[int.from_bytes(digest[:2], "big") % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [round(v / norm, 6) for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def index_document(db: Session, doc: KbDoc, size: int = 500) -> int:
    db.query(KbChunk).filter(KbChunk.doc_id == doc.id).delete()
    chunks = split_text(doc.content, size)
    for i, text in enumerate(chunks):
        db.add(KbChunk(
            id=f"{doc.id}_c{i:03d}", doc_id=doc.id, idx=i + 1,
            text=text, vector_json=json.dumps(vectorize(text)),
        ))
    doc.chunks = len(chunks)
    doc.status = "已索引"
    db.commit()
    return len(chunks)


def retrieve(db: Session, query: str, top_k: int = 4) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    qvec = vectorize(q)
    terms = [t for t in re.split(r"[\s,，、；;]+", q) if t]
    rows = db.query(KbChunk).all()
    scored: list[dict] = []
    for row in rows:
        vec = json.loads(row.vector_json or "[]")
        sim = cosine(qvec, vec)
        hit = sum(1 for t in terms if t in row.text) / max(1, len(terms))
        score = 0.6 * sim + 0.4 * hit
        scored.append({"row": row, "score": score, "sim": sim, "hit": hit})
    scored.sort(key=lambda x: x["score"], reverse=True)
    out = []
    for item in scored[:max(1, top_k)]:
        row = item["row"]
        out.append({
            "id": row.id, "doc": row.doc_id, "idx": row.idx, "text": row.text,
            "score": round(item["score"], 4),
            "vec": round(item["sim"], 4),
            "hit": round(item["hit"], 4),
        })
    return out
