from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView


class LocalBrowserExecutor(QMainWindow):
    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.workspace = workspace.resolve()
        self.event_dir = self.workspace / "data" / "agent_events"
        self.cache_dir = self.workspace / "data" / "cache" / "browser"
        self.request_path = self.event_dir / "browser_requests.jsonl"
        self.response_path = self.event_dir / "browser_responses.jsonl"
        self.ready_path = self.event_dir / "browser_executor.ready"
        self.pid_path = self.event_dir / "browser_executor.pid"
        self._position = self.request_path.stat().st_size if self.request_path.is_file() else 0
        self._busy = False
        self._pending: list[dict[str, Any]] = []

        self.view = QWebEngineView()
        self.setCentralWidget(self.view)
        self.resize(1200, 820)
        self.setWindowTitle("Shinsekai Local Browser Executor")

        self.event_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        self.ready_path.write_text(str(time.time()), encoding="utf-8")

        self._timer = QTimer(self)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self._poll_requests)
        self._timer.start()

    def _poll_requests(self) -> None:
        if self.request_path.is_file():
            with self.request_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._position)
                for line in handle:
                    payload = self._parse_line(line)
                    if payload is not None:
                        self._pending.append(payload)
                self._position = handle.tell()
        self._run_next()

    def _run_next(self) -> None:
        if self._busy or not self._pending:
            return
        request = self._pending.pop(0)
        self._busy = True
        action = str(request.get("action") or "")
        args = request.get("arguments")
        if not isinstance(args, dict):
            args = {}
        try:
            if action == "open_url":
                self._open_url(request, args)
            elif action == "screenshot":
                self._screenshot(request)
            elif action == "extract_text":
                self._extract_text(request, args)
            elif action == "observe":
                self._observe(request, args)
            elif action == "click":
                self._click(request, args)
            elif action == "type_text":
                self._type_text(request, args)
            else:
                self._finish(request, False, f"未知浏览器动作：{action}")
        except Exception as exc:
            self._finish(request, False, f"浏览器动作失败：{exc}")

    def _open_url(self, request: dict[str, Any], args: dict[str, Any]) -> None:
        url = str(args.get("url") or "").strip()
        if not url:
            self._finish(request, False, "URL 不能为空。")
            return
        scheme_match = re.match(r"^([a-z][a-z0-9+.-]*)://", url, re.I)
        if scheme_match and scheme_match.group(1).lower() not in {"http", "https", "file"}:
            self._finish(request, False, "只支持 http、https 或 file URL。")
            return
        if not scheme_match:
            if re.match(r"^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/.*)?$", url, re.I):
                url = "http://" + url
            else:
                url = "https://" + url

        finished = False

        def complete(ok: bool, timed_out: bool = False) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            try:
                self.view.loadFinished.disconnect(loaded)
            except Exception:
                pass
            title = self.view.title()
            current_url = self.view.url().toString()
            summary = "网页打开超时。" if timed_out else ("网页已打开。" if ok else "网页打开失败。")
            self._finish(
                request,
                ok,
                summary,
                detail=f"title: {title}\nurl: {current_url}",
                data={"title": title, "url": current_url},
            )

        def loaded(ok: bool) -> None:
            complete(ok)

        self.show()
        self.raise_()
        self.view.loadFinished.connect(loaded)
        self.view.load(QUrl(url))
        QTimer.singleShot(30000, lambda: complete(False, timed_out=True))

    def _screenshot(self, request: dict[str, Any]) -> None:
        self.show()
        self.raise_()
        output = self._capture_screenshot(str(request.get("id", "browser")))
        pixmap = self.view.grab()
        ok = pixmap.save(str(output))
        rel = output.relative_to(self.workspace).as_posix()
        self._finish(
            request,
            ok,
            "截图已保存。" if ok else "截图保存失败。",
            detail=rel,
            data={"path": rel},
            artifacts=[rel],
        )

    def _capture_screenshot(self, stem: str) -> Path:
        safe_stem = re.sub(r"[^a-zA-Z0-9_.-]+", "-", stem).strip("-") or "browser"
        return self.cache_dir / f"{safe_stem}.png"

    def _extract_text(self, request: dict[str, Any], args: dict[str, Any]) -> None:
        script = """
(() => JSON.stringify({
  title: document.title || '',
  url: location.href,
  readyState: document.readyState || '',
  text: ((document.body && document.body.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim()).slice(0, 9000)
}))()
"""
        fallback_script = """
(() => JSON.stringify({
  title: document.title || '',
  url: location.href,
  readyState: document.readyState || '',
  text: Array.from(document.querySelectorAll('body *'))
    .map(el => (el.innerText || el.textContent || '').trim())
    .filter(Boolean)
    .join('\\n')
    .replace(/\\n{3,}/g, '\\n\\n')
    .slice(0, 9000)
}))()
"""

        def parse_result(result: Any) -> dict[str, Any] | None:
            if isinstance(result, str):
                try:
                    payload = json.loads(result)
                    return payload if isinstance(payload, dict) else None
                except Exception:
                    return {"title": self.view.title(), "url": self.view.url().toString(), "text": result}
            if isinstance(result, dict):
                return result
            return None

        def finish_from_payload(result: Any) -> bool:
            payload = parse_result(result)
            if not payload:
                return False
            text = str(payload.get("text") or "").strip()
            if not text:
                return False
            detail = f"title: {payload.get('title','')}\nurl: {payload.get('url','')}\nreadyState: {payload.get('readyState','')}\n\n{text}"
            self._finish(request, True, "页面文本已提取。", detail=detail, data=payload)
            return True

        def fallback_done(result: Any) -> None:
            if finish_from_payload(result):
                return
            output = self.cache_dir / f"{request.get('id', 'browser')}-extract-fallback.png"
            pixmap = self.view.grab()
            saved = pixmap.save(str(output))
            rel = output.relative_to(self.workspace).as_posix()
            detail = (
                f"title: {self.view.title()}\n"
                f"url: {self.view.url().toString()}\n"
                f"fallback_screenshot: {rel if saved else ''}"
            )
            self._finish(
                request,
                False,
                "页面文本提取失败，已保存当前页面截图。" if saved else "页面文本提取失败。",
                detail=detail,
                data={"title": self.view.title(), "url": self.view.url().toString(), "screenshot": rel if saved else ""},
                artifacts=[rel] if saved else [],
            )

        def done(result: Any) -> None:
            if finish_from_payload(result):
                return
            self.view.page().runJavaScript(fallback_script, fallback_done)

        self.view.page().runJavaScript(script, done)

    def _legacy_extract_text_script(self) -> str:
        return """
(() => {
  const text = (document.body && document.body.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();
  return {
    title: document.title || '',
    url: location.href,
    text: text.slice(0, 6000)
  };
})()
"""

    def _observe(self, request: dict[str, Any], args: dict[str, Any]) -> None:
        script = """
(() => {
  const visible = (el, rect) => {
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden'
      && style.display !== 'none'
      && Number(style.opacity || 1) > 0
      && rect.width > 3
      && rect.height > 3
      && rect.bottom >= 0
      && rect.right >= 0
      && rect.top <= innerHeight
      && rect.left <= innerWidth;
  };
  const labelFor = (el) => {
    const direct = [
      el.innerText || '',
      el.value || '',
      el.getAttribute('aria-label') || '',
      el.getAttribute('alt') || '',
      el.placeholder || '',
      el.title || ''
    ].join(' ').replace(/\\s+/g, ' ').trim();
    return direct.slice(0, 180);
  };
  const important = new Set(['A', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'IMG', 'H1', 'H2', 'H3', 'P', 'LI']);
  const nodes = Array.from(document.querySelectorAll('body *'));
  const elements = [];
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    if (!visible(el, rect)) continue;
    const text = labelFor(el);
    const role = el.getAttribute('role') || '';
    const clickable = important.has(el.tagName) || !!role || el.onclick || el.tabIndex >= 0;
    if (!text && !clickable) continue;
    elements.push({
      tag: el.tagName.toLowerCase(),
      role,
      text,
      x: Math.round(rect.left),
      y: Math.round(rect.top),
      w: Math.round(rect.width),
      h: Math.round(rect.height)
    });
    if (elements.length >= 90) break;
  }
  return JSON.stringify({
    title: document.title || '',
    url: location.href,
    readyState: document.readyState || '',
    viewport: {
      width: innerWidth,
      height: innerHeight,
      scrollX,
      scrollY,
      devicePixelRatio
    },
    elements
  });
})()
"""

        def done(result: Any) -> None:
            payload: dict[str, Any] = {}
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        payload = parsed
                except Exception:
                    payload = {}
            elif isinstance(result, dict):
                payload = result

            output = self._capture_screenshot(f"{request.get('id', 'browser')}-observe")
            pixmap = self.view.grab()
            saved = pixmap.save(str(output))
            rel = output.relative_to(self.workspace).as_posix()
            elements = payload.get("elements") if isinstance(payload.get("elements"), list) else []
            lines = [
                f"title: {payload.get('title') or self.view.title()}",
                f"url: {payload.get('url') or self.view.url().toString()}",
                f"readyState: {payload.get('readyState') or ''}",
                f"screenshot: {rel if saved else ''}",
                f"viewport: {json.dumps(payload.get('viewport') or {}, ensure_ascii=False)}",
                "",
                "visible_elements:",
            ]
            for index, element in enumerate(elements[:60], start=1):
                text = str(element.get("text") or "").replace("\n", " ").strip()
                lines.append(
                    f"{index}. [{element.get('tag') or '?'} role={element.get('role') or ''} "
                    f"x={element.get('x')} y={element.get('y')} w={element.get('w')} h={element.get('h')}] {text}"
                )
            payload["screenshot"] = rel if saved else ""
            self._finish(
                request,
                bool(saved or elements),
                "页面视觉观察已完成。" if saved or elements else "页面视觉观察失败。",
                detail="\n".join(lines),
                data=payload,
                artifacts=[rel] if saved else [],
            )

        self.show()
        self.raise_()
        self.view.page().runJavaScript(script, done)


    def _click(self, request: dict[str, Any], args: dict[str, Any]) -> None:
        target = str(args.get("target") or "").strip()
        if not target:
            self._finish(request, False, "点击目标不能为空。")
            return
        script = self._find_element_script(target, "click")

        def done(result: Any) -> None:
            ok = isinstance(result, dict) and bool(result.get("ok"))
            self._finish(
                request,
                ok,
                "已点击页面元素。" if ok else "没有找到可点击的页面元素。",
                detail=json.dumps(result, ensure_ascii=False, indent=2),
                data=result if isinstance(result, dict) else {},
            )

        self.view.page().runJavaScript(script, done)

    def _type_text(self, request: dict[str, Any], args: dict[str, Any]) -> None:
        text = str(args.get("text") or "")
        target = str(args.get("target") or "").strip()
        script = self._type_script(text, target)

        def done(result: Any) -> None:
            ok = isinstance(result, dict) and bool(result.get("ok"))
            self._finish(
                request,
                ok,
                "文本已输入。" if ok else "没有找到可输入的位置。",
                detail=json.dumps(result, ensure_ascii=False, indent=2),
                data=result if isinstance(result, dict) else {},
            )

        self.view.page().runJavaScript(script, done)

    def _finish(
        self,
        request: dict[str, Any],
        ok: bool,
        summary: str,
        detail: str = "",
        data: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
    ) -> None:
        response = {
            "request_id": request.get("id"),
            "action": request.get("action"),
            "ok": ok,
            "summary": summary,
            "detail": detail,
            "data": data or {},
            "artifacts": artifacts or [],
            "finished_at": time.time(),
        }
        with self.response_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(response, ensure_ascii=False) + "\n")
        self._busy = False
        QTimer.singleShot(0, self._run_next)

    @staticmethod
    def _parse_line(line: str) -> dict[str, Any] | None:
        line = line.strip()
        if not line:
            return None
        try:
            payload = json.loads(line)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _find_element_script(target: str, action: str) -> str:
        return f"""
(() => {{
  const target = {json.dumps(target, ensure_ascii=False)};
  const safeMatches = (el, selector) => {{
    try {{ return !!selector && !!el.matches && el.matches(selector); }}
    catch (_) {{ return false; }}
  }};
  const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  let selected = null;
  try {{ selected = document.querySelector(target); }} catch (_) {{}}
  const matches = (el) => {{
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim();
    return text === target || text.includes(target) || safeMatches(el, target);
  }};
  const nodes = Array.from(document.querySelectorAll('button,a,input,textarea,[role="button"],[aria-label],select,summary,label'));
  const el = selected || nodes.filter(isVisible).find(matches) || nodes.find(matches);
  if (!el) return {{ ok: false, target }};
  el.scrollIntoView({{ block: 'center', inline: 'center' }});
  el.focus?.();
  el.click?.();
  return {{ ok: true, target, tag: el.tagName, text: (el.innerText || el.value || el.getAttribute('aria-label') || '').slice(0, 160) }};
}})()
"""

    @staticmethod
    def _type_script(text: str, target: str) -> str:
        return f"""
(() => {{
  const text = {json.dumps(text, ensure_ascii=False)};
  const target = {json.dumps(target, ensure_ascii=False)};
  const safeMatches = (el, selector) => {{
    try {{ return !!selector && !!el.matches && el.matches(selector); }}
    catch (_) {{ return false; }}
  }};
  const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const labelFor = (el) => {{
    const cssEscape = (value) => window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/["\\\\]/g, '\\\\$&');
    const direct = [
      el.getAttribute('aria-label') || '',
      el.placeholder || '',
      el.name || '',
      el.id || '',
      el.title || ''
    ];
    if (el.id) {{
      const label = document.querySelector(`label[for="${{cssEscape(el.id)}}"]`);
      if (label) direct.push(label.innerText || '');
    }}
    const parentLabel = el.closest?.('label');
    if (parentLabel) direct.push(parentLabel.innerText || '');
    return direct.join(' ').trim();
  }};
  const candidates = Array.from(document.querySelectorAll('input,textarea,[contenteditable="true"],[role="textbox"],[aria-label]'));
  const matches = (el) => {{
    if (!target) return true;
    const label = labelFor(el);
    return label === target || label.includes(target) || safeMatches(el, target);
  }};
  let selected = null;
  try {{ selected = target ? document.querySelector(target) : null; }} catch (_) {{}}
  const el = selected || candidates.filter(isVisible).find(matches) || candidates.find(matches) || document.activeElement;
  if (!el) return {{ ok: false, target }};
  el.scrollIntoView?.({{ block: 'center', inline: 'center' }});
  el.focus?.();
  if ('value' in el) {{
    el.value = text;
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }} else {{
    el.textContent = text;
    el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: text }}));
  }}
  return {{ ok: true, target, tag: el.tagName }};
}})()
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Shinsekai local browser executor.")
    parser.add_argument("--workspace", default=".", help="Project workspace")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = LocalBrowserExecutor(Path(args.workspace))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
