"""工具执行运行时：统一内置 / HTTP / Jinja2 三类实现。

调用顺序：
1. 内置工具（``builtin_tools.TOOL_REGISTRY``）直接执行；
2. 已注册的 HTTP 工具按 ``endpoint / method / headers`` 发起请求；
3. Jinja2 工具用 ``endpoint`` 作为模板渲染（适合报文拼装与轻量转换）。

所有异常统一映射为 ``AH-TOOL-*`` 错误码，调用方只需要记录一次失败。
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from jinja2 import Template
from sqlalchemy.orm import Session

from . import builtin_tools
from .errors import ApiError, ToolFailed, ToolTimeout
from .models import Tool

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate_params(schema_json: str | dict | None, params: dict) -> list[str]:
    """轻量 JSON Schema 校验（object / required / properties / type / enum）。

    只覆盖本平台实际使用的子集；未声明的字段按宽松口径放行，
    避免外部报文新增字段时被误判为参数错误。
    """
    if isinstance(schema_json, str):
        try:
            schema = json.loads(schema_json or "{}")
        except json.JSONDecodeError:
            return ["参数 Schema 不是合法 JSON"]
    else:
        schema = schema_json or {}
    if not isinstance(schema, dict) or not schema:
        return []
    if not isinstance(params, dict):
        return ["参数必须是 JSON 对象"]

    errors: list[str] = []
    for name in schema.get("required") or []:
        if name not in params:
            errors.append(f"缺少必填参数：{name}")
    for name, spec in (schema.get("properties") or {}).items():
        if name not in params or not isinstance(spec, dict):
            continue
        expect = spec.get("type")
        checker = _TYPE_CHECKS.get(expect or "")
        if checker and not checker(params[name]):
            errors.append(f"参数 {name} 类型应为 {expect}")
            continue
        if "enum" in spec and params[name] not in (spec.get("enum") or []):
            errors.append(f"参数 {name} 取值必须是 {spec['enum']} 之一")
        if expect == "array" and isinstance(params[name], list):
            item_type = (spec.get("items") or {}).get("type")
            item_checker = _TYPE_CHECKS.get(item_type or "")
            if item_checker and any(not item_checker(v) for v in params[name]):
                errors.append(f"参数 {name} 的元素类型应为 {item_type}")
    return errors


def _headers(tool: Tool) -> dict[str, str]:
    try:
        data = json.loads(tool.headers_json or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _http(tool: Tool, params: dict) -> Any:
    endpoint = (tool.endpoint or "").strip()
    if not endpoint:
        raise ApiError(f"HTTP 工具未配置 endpoint：{tool.name}", code="AH-TOOL-422",
                       status_code=422)
    if not endpoint.lower().startswith(("http://", "https://")):
        raise ApiError(f"endpoint 必须为 http(s) 地址：{endpoint}", code="AH-TOOL-422",
                       status_code=422)
    method = (tool.method or "POST").upper()
    if method not in _METHODS:
        raise ApiError(f"不支持的 HTTP 方法：{method}", code="AH-TOOL-422", status_code=422)

    timeout = max(1, int(tool.timeout_s or 10))
    try:
        with httpx.Client(timeout=timeout) as client:
            if method == "GET":
                resp = client.get(endpoint, params=params, headers=_headers(tool))
            else:
                resp = client.request(method, endpoint, json=params, headers=_headers(tool))
            resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ToolTimeout(f"工具执行超时（>{timeout}s）：{tool.name}") from exc
    except httpx.HTTPStatusError as exc:
        raise ToolFailed(f"HTTP {exc.response.status_code}：{exc.response.text[:160]}",
                         status_code=502) from exc
    except httpx.HTTPError as exc:
        raise ToolFailed(f"HTTP 调用失败：{type(exc).__name__}: {exc}", status_code=502) from exc

    content_type = (resp.headers.get("content-type") or "").lower()
    if "json" in content_type:
        try:
            return resp.json()
        except ValueError:
            pass
    text = resp.text or ""
    return {"status_code": resp.status_code, "text": text[:4000]}


def _jinja(tool: Tool, params: dict, ctx: dict) -> Any:
    template = tool.endpoint or ""
    if not template:
        raise ApiError(f"Jinja2 工具未配置模板：{tool.name}", code="AH-TOOL-422",
                       status_code=422)
    try:
        rendered = Template(template).render(params=params, ctx=ctx, **params)
    except Exception as exc:
        raise ToolFailed(f"模板渲染失败：{exc}", status_code=500) from exc
    return {"rendered": rendered[:4000]}


def execute(tool: Tool, params: dict, ctx: dict | None = None) -> Any:
    """按工具实现类型执行。调用方负责记录 ToolCall 与耗时。"""
    if tool.status != "启用":
        raise ApiError(f"工具已停用：{tool.name}", code="AH-TOOL-409", status_code=409)
    errors = validate_params(tool.schema_json, params)
    if errors:
        raise ApiError("；".join(errors), code="AH-TOOL-422", status_code=422)
    impl = (tool.impl or "").strip().lower()
    if impl == "http":
        return _http(tool, params)
    if impl == "jinja2":
        return _jinja(tool, params, ctx or {})
    if impl in ("python", "llm"):
        raise ApiError(f"该工具没有内置实现：{tool.name}", code="AH-TOOL-501", status_code=501)
    raise ApiError(f"暂不支持的实现类型 {tool.impl}：{tool.name}", code="AH-TOOL-501",
                   status_code=501)


def invoke_by_name(db: Session | None, name: str, payload: dict, ctx: dict | None = None) -> Any:
    """按工具名执行：内置 / 外部工具统一先查数据库状态。

    供 DAG 工具节点与控制台「调用测试」共用，保证两条路径行为一致：
    工具一旦停用，即使有内置实现也不再执行。
    """
    tool = db.query(Tool).filter(Tool.name == name).first() if db is not None else None
    if tool is not None and tool.status != "启用":
        raise ApiError(f"工具已停用：{name}", code="AH-TOOL-409", status_code=409)
    if name in builtin_tools.TOOL_REGISTRY:
        schema = tool.schema_json if tool is not None else builtin_tools.schema_for(name)
        errors = validate_params(schema, payload)
        if errors:
            raise ApiError("；".join(errors), code="AH-TOOL-422", status_code=422)
        return builtin_tools.invoke(name, payload, ctx or {})
    if not tool:
        raise KeyError(f"未注册的工具：{name}")
    return execute(tool, payload, ctx)
