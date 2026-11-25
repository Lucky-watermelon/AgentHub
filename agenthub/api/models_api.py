"""模型接入接口：Provider、别名、降级链与调用统计。"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import llm
from ..config import settings
from ..db import get_db
from ..errors import ApiError, NotFound
from ..models import Alias, Provider, now
from ..runner import uid
from ..schemas import (
    AliasCreate,
    AliasUpdate,
    ProviderCreate,
    RoutingUpdate,
    alias_out,
    provider_out,
)

router = APIRouter()


def _probe(provider: Provider) -> tuple[bool, str, int]:
    """探测 Provider 可用性。

    - Mock 模式 / 规则 Provider：不出网，按本地可用处理；
    - 其余 Provider：请求 ``GET {base_url}/models``，用真实响应判断健康状态。
    """
    if provider.kind == "规则" or not provider.base_url:
        return True, "本地规则 Provider，无需出网", 0
    if settings.llm_mock:
        return True, "AH_LLM_MOCK=true，跳过真实探测", 0
    url = provider.base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        dur = int((time.perf_counter() - started) * 1000)
        count = len(data.get("data") or []) if isinstance(data, dict) else 0
        return True, f"GET /models 返回 200，{count} 个模型", dur
    except httpx.HTTPStatusError as exc:
        dur = int((time.perf_counter() - started) * 1000)
        return False, f"HTTP {exc.response.status_code}：{exc.response.text[:120]}", dur
    except Exception as exc:
        dur = int((time.perf_counter() - started) * 1000)
        return False, f"{type(exc).__name__}: {exc}"[:200], dur


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)):
    out = []
    for p in db.query(Provider).order_by(Provider.name).all():
        item = provider_out(p)
        item["api_key_set"] = bool(p.api_key)
        out.append(item)
    return out


@router.post("/providers", status_code=201)
def create_provider(body: ProviderCreate, db: Session = Depends(get_db)):
    if db.query(Provider).filter(Provider.name == body.name).first():
        raise ApiError(f"Provider 已存在：{body.name}", code="AH-MODEL-409", status_code=409)
    if body.base_url and not body.base_url.lower().startswith(("http://", "https://")):
        raise ApiError("base_url 必须是 http(s) 地址", code="AH-MODEL-422", status_code=422)
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
    if "name" in payload and payload["name"] != provider.name:
        new_name = payload["name"]
        if db.query(Provider).filter(Provider.name == new_name).first():
            raise ApiError(f"Provider 已存在：{new_name}", code="AH-MODEL-409", status_code=409)
        # 别名按 Provider 名称解析，改名时同步级联，避免别名失效
        db.query(Alias).filter(Alias.provider_name == provider.name).update(
            {Alias.provider_name: new_name})
        provider.name = new_name
    if "base_url" in payload and payload["base_url"]:
        if not str(payload["base_url"]).lower().startswith(("http://", "https://")):
            raise ApiError("base_url 必须是 http(s) 地址", code="AH-MODEL-422", status_code=422)
    for field in ("kind", "base_url", "models", "caps", "status", "api_key"):
        if field in payload:
            setattr(provider, field, payload[field])
    provider.updated_at = now()
    db.commit()
    return provider_out(provider)


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: str, db: Session = Depends(get_db)):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise NotFound("Provider 不存在")
    refs = db.query(Alias).filter(Alias.provider_name == provider.name).count()
    if refs:
        raise ApiError(f"仍有 {refs} 个别名指向该 Provider，先迁移别名再删除",
                       code="AH-MODEL-409", status_code=409)
    db.delete(provider)
    db.commit()
    return {"ok": True}


@router.post("/providers/{provider_id}/check")
def check_provider(provider_id: str, db: Session = Depends(get_db)):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise NotFound("Provider 不存在")
    if provider.status == "stopped":
        raise ApiError(f"{provider.name} 已停用，请先启用", code="AH-MODEL-503", status_code=503)
    ok, detail, dur = _probe(provider)
    provider.status = "healthy" if ok else "异常"
    provider.updated_at = now()
    db.commit()
    return {"ok": ok, "detail": detail, "latency_ms": dur, "provider": provider_out(provider)}


@router.get("/aliases")
def list_aliases(db: Session = Depends(get_db)):
    chain = llm.get_chain(db)
    out = []
    for a in db.query(Alias).all():
        item = alias_out(a)
        item["in_chain"] = a.alias in chain
        out.append(item)
    return out


@router.post("/aliases", status_code=201)
def create_alias(body: AliasCreate, db: Session = Depends(get_db)):
    if db.query(Alias).filter(Alias.alias == body.alias).first():
        raise ApiError(f"别名已存在：{body.alias}", code="AH-MODEL-409", status_code=409)
    if not db.query(Provider).filter(Provider.name == body.provider_name).first():
        raise ApiError(f"Provider 不存在：{body.provider_name}",
                       code="AH-MODEL-422", status_code=422)
    alias = Alias(id=uid("al"), alias=body.alias, provider_name=body.provider_name,
                  model=body.model or body.alias, caps=body.caps, status="启用")
    db.add(alias)
    db.commit()
    return alias_out(alias)


@router.patch("/aliases/{alias_name}")
def update_alias(alias_name: str, body: AliasUpdate, db: Session = Depends(get_db)):
    """调整别名指向（切换模型只改这里，不动 DAG 与技能配置）。"""
    alias = db.query(Alias).filter(Alias.alias == alias_name).first()
    if not alias:
        raise NotFound("别名不存在")
    if body.provider_name:
        if not db.query(Provider).filter(Provider.name == body.provider_name).first():
            raise ApiError(f"Provider 不存在：{body.provider_name}",
                           code="AH-MODEL-422", status_code=422)
        alias.provider_name = body.provider_name
    if body.model is not None:
        alias.model = body.model
    if body.caps is not None:
        alias.caps = body.caps
    if body.status is not None:
        alias.status = body.status
    db.commit()
    return alias_out(alias)


@router.delete("/aliases/{alias_name}")
def delete_alias(alias_name: str, db: Session = Depends(get_db)):
    alias = db.query(Alias).filter(Alias.alias == alias_name).first()
    if not alias:
        raise NotFound("别名不存在")
    if alias_name in llm.get_chain(db):
        raise ApiError("别名仍在降级链中，先从链路移除再删除", code="AH-MODEL-409", status_code=409)
    was_default = alias.status == "默认"
    db.delete(alias)
    db.commit()
    promoted = ""
    if was_default:
        sibling = (db.query(Alias)
                     .filter(Alias.caps.contains("聊天"))
                     .order_by(Alias.id).first())
        if sibling:
            sibling.status = "默认"
            db.commit()
            promoted = sibling.alias
    return {"ok": True, "promoted": promoted}


@router.post("/aliases/{alias_name}/default")
def set_default_alias(alias_name: str, db: Session = Depends(get_db)):
    alias = db.query(Alias).filter(Alias.alias == alias_name).first()
    if not alias:
        raise NotFound("别名不存在")
    is_chat = "聊天" in (alias.caps or "")
    for sibling in db.query(Alias).filter(Alias.status == "默认").all():
        if sibling.id != alias.id and ("聊天" in (sibling.caps or "")) == is_chat:
            sibling.status = "启用"
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
    chain: list[str] = []
    for name in body.chain:
        if name and name not in chain:
            chain.append(name)
    if not chain:
        raise ApiError("降级链不能为空", code="AH-MODEL-422", status_code=422)
    known = {a.alias for a in db.query(Alias).all()}
    unknown = [c for c in chain if c not in known]
    if unknown:
        raise ApiError("未知别名：" + "、".join(unknown), code="AH-MODEL-422", status_code=422)
    llm.set_chain(db, chain)
    return {"ok": True, "chain": chain}


@router.post("/routing/test")
def test_routing(db: Session = Depends(get_db)):
    """按降级链依次模拟调用，返回每一级的判定结果。"""
    chain = llm.get_chain(db)
    steps = []
    for name in chain:
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


@router.get("/models/stats")
def model_stats(db: Session = Depends(get_db)):
    """别名调用统计（进程内）与 Provider 健康概览。"""
    providers = [{"name": p.name, "kind": p.kind, "status": p.status,
                  "api_key_set": bool(p.api_key)} for p in db.query(Provider).all()]
    return {"chain": llm.get_chain(db), "aliases": llm.stats(), "providers": providers}
