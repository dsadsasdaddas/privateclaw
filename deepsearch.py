import asyncio
from playwright.async_api import async_playwright, Page
from langgraph.graph import StateGraph, START, END

class DeepSearch:
    def __init__(self, client):
        self.client = client
        self.page: Page = None
        self.visited_urls = set()
        self.brower_context = None
        self.app = self.build_graph()
    

    def _build_graph(self):
        workflow = StateGraph(AgentState)




        return workflow.compile()
    
    async def _init_browser(self):
        p = await async_playwright().start()