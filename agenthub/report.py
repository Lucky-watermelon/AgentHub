"""HTML 全景分析报告渲染（Jinja2）。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _pct(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _money(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def render_report(customer_no: str, payload: dict, features: dict, summary: str = "") -> str:
    tpl = _env.get_template("report.html")
    credit = features.get("credit") or {}
    finance = features.get("finance") or {}
    judicial = features.get("judicial") or {}
    flags = (credit.get("risk_flags") or []) + (finance.get("risk_flags") or [])
    return tpl.render(
        customer_no=customer_no or "—",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        summary=summary or "暂无模型结论",
        credit=credit,
        finance=finance,
        judicial=judicial,
        ai=features.get("ai") or {},
        flags=flags,
        payload=payload,
        pct=_pct,
        money=_money,
    )


def save_report(html: str, customer_no: str, run_id: str) -> str:
    """写入 reports/ 目录，返回相对路径。"""
    settings.reports_path.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in (customer_no or "customer") if c.isalnum() or c in "-_") or "customer"
    name = f"{safe}_{run_id}.html"
    path = settings.reports_path / name
    path.write_text(html, encoding="utf-8")
    latest = settings.reports_path / f"{safe}.html"
    latest.write_text(html, encoding="utf-8")
    return f"reports/{name}"
