from __future__ import annotations

from dataclasses import dataclass
import re
import time

from agent_companion.core.schemas import AgentPlan, ToolRequest, ToolResult


@dataclass
class DesktopContext:
    browser: str = ""
    site: str = ""
    url: str = ""
    updated_at: float = 0.0


def active_desktop_context(context: DesktopContext, *, ttl_seconds: float, now: float | None = None) -> DesktopContext | None:
    if not context.site:
        return None
    clock = time.time() if now is None else now
    if clock - context.updated_at > ttl_seconds:
        return None
    return context


def rewrite_plan_for_desktop_context(plan: AgentPlan, context: DesktopContext) -> AgentPlan:
    if not _looks_like_search_command(plan.user_text):
        return plan
    if not plan.steps:
        return plan
    step = plan.steps[0]
    if step.name not in {"browser.search", "observe.screen"}:
        return plan
    if _requests_global_browser_search(plan.user_text):
        return plan
    query = _desktop_context_search_query(plan.user_text, step)
    if not query:
        return plan
    return AgentPlan(
        task_id=plan.task_id,
        user_text=plan.user_text,
        intent="desktop_workflow",
        steps=[
            ToolRequest(
                "computer.workflow",
                {
                    "workflow": "open_web_search",
                    "browser": context.browser or "edge",
                    "site": context.site,
                    "query": query,
                },
                "继续在当前桌面浏览器站点中搜索，需要确认。",
            )
        ],
    )


def record_desktop_context(step: ToolRequest, result: ToolResult, *, now: float | None = None) -> DesktopContext | None:
    if step.name != "computer.workflow":
        return None
    if not result.ok:
        return None
    workflow = str(step.arguments.get("workflow") or "").strip()
    if workflow in {"open_web_search", "open_url"}:
        raw_site = str(step.arguments.get("site") or "").strip()
        url = str(step.arguments.get("url") or "").strip()
        site = raw_site
        if "://" in raw_site:
            url = raw_site
            site = _site_from_url(raw_site)
        if not site and url:
            site = _site_from_url(url)
        return DesktopContext(
            browser=str(step.arguments.get("browser") or "edge").strip() or "edge",
            site=site,
            url=url,
            updated_at=time.time() if now is None else now,
        )
    if workflow == "open_app":
        return DesktopContext()
    return None


def _looks_like_search_command(text: str) -> bool:
    return re.search(r"(?:搜索|搜一下|查找|搜(?!集))\s*\S+", text) is not None


def _requests_global_browser_search(text: str) -> bool:
    lowered = text.casefold()
    return any(token in lowered for token in ("baidu", "google", "chrome")) or any(token in text for token in ("百度", "谷歌", "全网搜索"))


def _desktop_context_search_query(user_text: str, step: ToolRequest) -> str:
    query = str(step.arguments.get("query") or "").strip() if isinstance(step.arguments, dict) else ""
    if not query:
        query = " ".join((user_text or "").strip().split())
    query = re.sub(r"^(?:帮我|请|麻烦)?(?:继续)?(?:在当前页面|在这个页面|在当前网站|在这里)?(?:搜索|搜一下|查找|搜(?!集))\s*", "", query).strip()
    query = re.sub(r"[。！？!?]+$", "", query).strip(" ：:，,")
    return query[:160]


def _site_from_url(url: str) -> str:
    lowered = url.casefold()
    if "bilibili.com" in lowered:
        return "bilibili"
    return ""
