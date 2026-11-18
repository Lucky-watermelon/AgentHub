"""模型接入接口：Provider、别名、降级链。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import llm
from ..db import get_db
from ..errors import ApiError, NotFound
from ..models import Alias, Provider, now
from ..runner import uid
from ..schemas import (
    AliasCreate,
    ProviderCreate,
    RoutingUpdate,
    alias_out,
    provider_out,
)

router = APIRouter()


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)):
    return [provider_out(p) for p in db.query(Provider).order_by(Provider.name).all()]


@router.post("/providers", status_code=201)
def create_provider(body: ProviderCreate, db: Session = Depends(get_db)):
    if db.query(Provider).filter(Provider.name == body.name).first():
        raise ApiError(f"Provider 已存在：{body.name}", code="AH-MODEL-409", status_code=409)
    provider = Provider(id=uid("prov"), name=body.name, kind=body.kind, base_url=body.base_url,
                        models=body.models, caps=body.caps, api_key=body.api_key, status="healthy")
    db.add(provider)
    db.commit()
    return provider_out(provider)


@router.patch("/providers/{provider_id}")
def update_provider(provider_id: str, payload: dict, db: Session = Depends(get_db)):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise NotFound("Provider 不存在")
    for field in ("base_url", "models", "caps", "status", "api_key"):
        if field in payload:
            setattr(provider, field, payload[field])
    provider.updated_at = now()
    db.commit()
    return provider_out(provider)


@router.post("/providers/{provider_id}/check")
def check_provider(provider_id: str, db: Session = Depends(get_db)):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise NotFound("Provider 不存在")
    if provider.status == "stopped":
        raise ApiError(f"{provider.name} 未启用", code="AH-MODEL-503", status_code=503)
    provider.status = "healthy"
    provider.updated_at = now()
    db.commit()
    return {"ok": True, "provider": provider_out(provider),
            "checked": "GET /models（本地 Mock 探测）"}


@router.get("/aliases")
def list_aliases(db: Session = Depends(get_db)):
    return [alias_out(a) for a in db.query(Alias).all()]


@router.post("/aliases", status_code=201)
def create_alias(body: AliasCreate, db: Session = Depends(get_db)):
    if db.query(Alias).filter(Alias.alias == body.alias).first():
        raise ApiError(f"别名已存在：{body.alias}", code="AH-MODEL-409", status_code=409)
    alias = Alias(id=uid("al"), alias=body.alias, provider_name=body.provider_name,
                  model=body.model or body.alias, caps=body.caps, status="启用")
    db.add(alias)
    db.commit()
    return alias_out(alias)


@router.post("/aliases/{alias_name}/default")
def set_default_alias(alias_name: str, db: Session = Depends(get_db)):
    alias = db.query(Alias).filter(Alias.alias == alias_name).first()
    if not alias:
        raise NotFound("别名不存在")
    db.query(Alias).filter(Alias.status == "默认").update({Alias.status: "启用"})
    alias.status = "默认"
    chain = llm.get_chain(db)
    chain = [alias_name] + [c for c in chain if c != alias_name]
    llm.set_chain(db, chain)
    db.commit()
    return {"ok": True, "chain": chain}


@router.get("/routing")
def get_routing(db: Session = Depends(get_db)):
    return {"chain": llm.get_chain(db)}


@router.put("/routing")
def update_routing(body: RoutingUpdate, db: Session = Depends(get_db)):
    known = {a.alias for a in db.query(Alias).all()}
    unknown = [c for c in body.chain if c not in known]
    if unknown:
        raise ApiError("未知别名：" + "、".join(unknown), code="AH-MODEL-422", status_code=422)
    llm.set_chain(db, body.chain)
    return {"ok": True, "chain": body.chain}


@router.post("/routing/test")
def test_routing(db: Session = Depends(get_db)):
    """按降级链依次模拟调用，返回每一级的判定结果。"""
    chain = llm.get_chain(db)
    steps = []
    for i, name in enumerate(chain):
        alias, provider = llm.resolve(db, name)
        if not alias or not provider:
            steps.append({"alias": name, "result": "FAIL", "reason": "别名或 Provider 不存在"})
            continue
        if provider.status == "stopped":
            steps.append({"alias": name, "result": "FAIL", "reason": "Provider 未启用"})
            continue
        steps.append({"alias": name, "result": "OK", "reason": f"{provider.name} / {alias.model}"})
        break
    if steps and all(s["result"] == "FAIL" for s in steps):
        raise ApiError("降级链全部失败", code="AH-MODEL-502", status_code=502)
    return {"chain": chain, "steps": steps}
