import asyncio
import json
from typing import Any, Dict, List, Optional, TypedDict

from ddgs import DDGS
from langgraph.graph import END, START, StateGraph
from playwright.async_api import BrowserContext, Page, Playwright, TimeoutError, async_playwright
import yaml


def _load_models() -> dict:
    defaults = {"plan": "qwen-max", "router": "qwen-max", "summary": "qwen-max"}
    try:
        with open("personalization.yaml", "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
            models = raw.get("models", {}) or {}
            defaults["plan"] = models.get("plan", defaults["plan"])
            defaults["router"] = models.get("router", defaults["router"])
            defaults["summary"] = models.get("summary", defaults["summary"])
    except Exception:
        pass
    return defaults


MODELS = _load_models()


class AgentState(TypedDict, total=False):
    query: str
    subqueries: List[str]
    search_results: List[Dict[str, Any]]
    pending_urls: List[str]
    current_url: str
    visited_urls: List[str]
    notes: List[Dict[str, str]]
    max_pages: int
    max_rounds: int
    search_round: int
    reflection: str
    action: str
    final_answer: str


class DeepSearch:
    """Deep-search workflow with reflection, multi-round web search and DOM drill-down."""

    def __init__(self, client):
        self.client = client
        self.page: Optional[Page] = None
        self.browser_context: Optional[BrowserContext] = None
        self.playwright: Optional[Playwright] = None
        self.app = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        workflow.add_node("plan_queries", self._plan_queries)
        workflow.add_node("search_web", self._search_web)
        workflow.add_node("pick_next_url", self._pick_next_url)
        workflow.add_node("read_page", self._read_page)
        workflow.add_node("reflect", self._reflect)
        workflow.add_node("summarize", self._summarize)

        workflow.add_edge(START, "plan_queries")
        workflow.add_edge("plan_queries", "search_web")
        workflow.add_edge("search_web", "pick_next_url")
        workflow.add_conditional_edges(
            "pick_next_url",
            self._route_after_pick,
            {"read_page": "read_page", "reflect": "reflect"},
        )
        workflow.add_edge("read_page", "reflect")
        workflow.add_conditional_edges(
            "reflect",
            self._route_after_reflect,
            {
                "pick_next_url": "pick_next_url",
                "search_web": "search_web",
                "summarize": "summarize",
            },
        )
        workflow.add_edge("summarize", END)

        return workflow.compile()

    async def _init_browser(self):
        if self.page:
            return
        self.playwright = await async_playwright().start()
        browser = await self.playwright.chromium.launch(headless=True)
        self.browser_context = await browser.new_context()
        self.page = await self.browser_context.new_page()

    async def _close_browser(self):
        if self.page:
            await self.page.close()
            self.page = None
        if self.browser_context:
            await self.browser_context.close()
            self.browser_context = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    def _plan_queries(self, state: AgentState) -> AgentState:
        query = state["query"]
        subqueries = [query]

        try:
            response = self.client.chat.completions.create(
                model=MODELS["plan"],
                messages=[
                    {
                        "role": "system",
                        "content": "你是搜索词规划助手。基于用户问题，给出3-5条更具体检索词，仅返回JSON数组字符串。",
                    },
                    {"role": "user", "content": query},
                ],
                temperature=0.2,
                stream=False,
            )
            content = (response.choices[0].message.content or "").strip()
            candidates = json.loads(content)
            if isinstance(candidates, list):
                cleaned = [str(x).strip() for x in candidates if str(x).strip()]
                if cleaned:
                    subqueries = cleaned[:5]
        except Exception:
            pass

        return {
            "subqueries": subqueries,
            "search_results": [],
            "pending_urls": [],
            "visited_urls": [],
            "notes": [],
            "search_round": 0,
            "reflection": "",
            "action": "",
        }

    def _search_web(self, state: AgentState) -> AgentState:
        seen = set(state.get("pending_urls", [])) | set(state.get("visited_urls", []))
        all_results: List[Dict[str, Any]] = list(state.get("search_results", []))

        for q in state.get("subqueries", []):
            with DDGS() as ddgs:
                rows = list(ddgs.text(q, max_results=6))
            for row in rows:
                url = row.get("href") or row.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                all_results.append(
                    {
                        "title": row.get("title", ""),
                        "snippet": row.get("body", ""),
                        "url": url,
                    }
                )

        return {
            "search_results": all_results,
            "pending_urls": [item["url"] for item in all_results],
            "search_round": state.get("search_round", 0) + 1,
        }

    def _pick_next_url(self, state: AgentState) -> AgentState:
        visited = set(state.get("visited_urls", []))
        max_pages = state.get("max_pages", 5)
        if len(visited) >= max_pages:
            return {"current_url": ""}

        for url in state.get("pending_urls", []):
            if url not in visited:
                visited.add(url)
                return {"current_url": url, "visited_urls": list(visited)}

        return {"current_url": "", "visited_urls": list(visited)}

    def _route_after_pick(self, state: AgentState) -> str:
        if state.get("current_url"):
            return "read_page"
        return "reflect"

    async def _explore_dom_links(self, base_url: str, notes: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """When content is poor, use DOM links and simulated clicks to dive deeper."""
        await self.page.goto(base_url, timeout=15000, wait_until="domcontentloaded")

        candidate_links = self.page.locator("main a, article a, a")
        count = min(await candidate_links.count(), 4)

        for i in range(count):
            try:
                await self.page.goto(base_url, timeout=15000, wait_until="domcontentloaded")
                link = self.page.locator("main a, article a, a").nth(i)
                text = (await link.inner_text()).strip()
                if len(text) < 2:
                    continue

                await link.scroll_into_view_if_needed(timeout=2000)
                old_url = self.page.url
                await link.click(timeout=4000)
                await self.page.wait_for_load_state("domcontentloaded", timeout=8000)
                new_url = self.page.url

                if new_url == old_url:
                    continue

                inner_text = (await self.page.inner_text("body")).strip()
                if len(inner_text) < 300:
                    continue

                notes.append(
                    {
                        "url": new_url,
                        "title": (await self.page.title()).strip() or f"DOM点击: {text}",
                        "content": inner_text[:3000],
                    }
                )
                break
            except Exception:
                continue

        return notes

    async def _read_page(self, state: AgentState) -> AgentState:
        url = state.get("current_url", "")
        notes = list(state.get("notes", []))
        if not url:
            return {"notes": notes}

        try:
            await self._init_browser()
            await self.page.goto(url, timeout=15000, wait_until="domcontentloaded")
            title = await self.page.title()
            text = (await self.page.inner_text("body")).strip()

            notes.append({"url": url, "title": title.strip(), "content": text[:3500]})

            # 反思触发条件：正文太少，尝试基于 DOM 结构模拟点击继续下钻。
            if len(text) < 300:
                notes = await self._explore_dom_links(url, notes)

        except TimeoutError as e:
            notes.append({"url": url, "title": "读取超时", "content": f"页面读取超时: {e}"})
        except Exception as e:
            notes.append({"url": url, "title": "读取失败", "content": f"无法读取页面内容: {e}"})

        return {"notes": notes}

    def _reflect(self, state: AgentState) -> AgentState:
        notes = state.get("notes", [])
        search_round = state.get("search_round", 0)
        max_rounds = state.get("max_rounds", 3)

        if not notes and search_round < max_rounds:
            return {"action": "search_web", "reflection": "没有拿到可用内容，触发二次搜索。"}

        if notes:
            last_note = notes[-1]
            content = last_note.get("content", "")
            failed = any(x in last_note.get("title", "") for x in ["失败", "超时"])
            shallow = len(content.strip()) < 180

            if (failed or shallow) and search_round < max_rounds:
                refined_query = self._generate_refined_query(state)
                subqueries = list(state.get("subqueries", []))
                if refined_query and refined_query not in subqueries:
                    subqueries.append(refined_query)
                return {
                    "action": "search_web",
                    "subqueries": subqueries[-6:],
                    "reflection": f"内容质量不足，新增检索词: {refined_query}",
                }

        pending = state.get("pending_urls", [])
        visited = set(state.get("visited_urls", []))
        has_unvisited = any(url not in visited for url in pending)

        if has_unvisited and len(visited) < state.get("max_pages", 5):
            return {"action": "pick_next_url", "reflection": "继续访问下一条结果。"}

        return {"action": "summarize", "reflection": "信息已足够，进入总结。"}

    def _generate_refined_query(self, state: AgentState) -> str:
        base_query = state.get("query", "")
        notes = state.get("notes", [])[-2:]
        note_text = "\n".join([f"{n.get('title','')} {n.get('content','')[:120]}" for n in notes])

        try:
            response = self.client.chat.completions.create(
                model=MODELS["router"],
                messages=[
                    {
                        "role": "system",
                        "content": "你是搜索反思器。根据失败线索给出一个更精准的新检索词，只输出一行纯文本。",
                    },
                    {
                        "role": "user",
                        "content": f"原问题: {base_query}\n失败线索: {note_text}\n请输出新检索词。",
                    },
                ],
                temperature=0.3,
                stream=False,
            )
            query = (response.choices[0].message.content or "").strip()
            return query if query else f"{base_query} 官方文档 详细说明"
        except Exception:
            return f"{base_query} 官方文档 详细说明"

    def _route_after_reflect(self, state: AgentState) -> str:
        action = state.get("action", "summarize")
        if action in {"pick_next_url", "search_web", "summarize"}:
            return action
        return "summarize"

    def _summarize(self, state: AgentState) -> AgentState:
        query = state.get("query", "")
        notes = state.get("notes", [])
        if not notes:
            return {"final_answer": "未检索到可用信息，请尝试更具体的问题。"}

        sources = "\n\n".join(
            [
                f"来源: {n.get('url', '')}\n标题: {n.get('title', '')}\n内容摘要: {n.get('content', '')}"
                for n in notes
            ]
        )

        try:
            response = self.client.chat.completions.create(
                model=MODELS["summary"],
                messages=[
                    {
                        "role": "system",
                        "content": "你是研究助理。请基于给定来源总结答案，先给结论，再列出要点，并附上来源链接。",
                    },
                    {
                        "role": "user",
                        "content": f"问题：{query}\n\n请基于以下资料回答：\n{sources}",
                    },
                ],
                temperature=0.2,
                stream=False,
            )
            answer = response.choices[0].message.content or ""
        except Exception:
            bullet_points = [f"- {n.get('title')}: {n.get('url')}" for n in notes]
            answer = "检索完成（LLM总结失败），可参考以下来源：\n" + "\n".join(bullet_points)

        reflection = state.get("reflection", "")
        if reflection:
            answer = f"[检索反思] {reflection}\n\n{answer}"

        return {"final_answer": answer}

    async def arun(self, query: str, max_pages: int = 5, max_rounds: int = 3) -> str:
        try:
            result = await self.app.ainvoke(
                {"query": query, "max_pages": max_pages, "max_rounds": max_rounds}
            )
            return result.get("final_answer", "")
        finally:
            await self._close_browser()

    def run(self, query: str, max_pages: int = 5, max_rounds: int = 3) -> str:
        return asyncio.run(self.arun(query=query, max_pages=max_pages, max_rounds=max_rounds))
