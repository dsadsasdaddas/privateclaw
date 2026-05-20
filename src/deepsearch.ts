import { chromium, errors, type Browser, type BrowserContext, type Page } from "playwright";
import { loadModels } from "./config.js";
import { searchWeb } from "./tools.js";
import type { SearchResult } from "./types.js";

interface DeepSearchNote {
  url: string;
  title: string;
  content: string;
}

interface AgentState {
  query: string;
  subqueries: string[];
  search_results: SearchResult[];
  pending_urls: string[];
  current_url: string;
  visited_urls: string[];
  notes: DeepSearchNote[];
  max_pages: number;
  max_rounds: number;
  search_round: number;
  reflection: string;
  action: "" | "pick_next_url" | "search_web" | "summarize";
  final_answer: string;
}

function uniqueTail(values: string[], limit: number): string[] {
  const out: string[] = [];
  for (const value of values) {
    if (value && !out.includes(value)) {
      out.push(value);
    }
  }
  return out.slice(-limit);
}

export class DeepSearch {
  private page: Page | undefined;
  private browserContext: BrowserContext | undefined;
  private browser: Browser | undefined;
  private readonly models: ReturnType<typeof loadModels>;

  constructor(
    private readonly client: any,
    private readonly projectRoot = process.cwd(),
  ) {
    this.models = loadModels(this.projectRoot);
  }

  private async initBrowser(): Promise<void> {
    if (this.page) {
      return;
    }
    this.browser = await chromium.launch({ headless: true });
    this.browserContext = await this.browser.newContext();
    this.page = await this.browserContext.newPage();
  }

  private async closeBrowser(): Promise<void> {
    if (this.page) {
      await this.page.close().catch(() => undefined);
      this.page = undefined;
    }
    if (this.browserContext) {
      await this.browserContext.close().catch(() => undefined);
      this.browserContext = undefined;
    }
    if (this.browser) {
      await this.browser.close().catch(() => undefined);
      this.browser = undefined;
    }
  }

  private async planQueries(state: AgentState): Promise<Partial<AgentState>> {
    let subqueries = [state.query];
    try {
      const response = await this.client.chat.completions.create({
        model: this.models.plan,
        messages: [
          {
            role: "system",
            content: "你是搜索词规划助手。基于用户问题，给出3-5条更具体检索词，仅返回JSON数组字符串。",
          },
          { role: "user", content: state.query },
        ],
        temperature: 0.2,
        stream: false,
      });
      const content = String(response.choices?.[0]?.message?.content ?? "").trim();
      const candidates = JSON.parse(content) as unknown;
      if (Array.isArray(candidates)) {
        const cleaned = candidates.map((item) => String(item).trim()).filter(Boolean);
        if (cleaned.length > 0) {
          subqueries = cleaned.slice(0, 5);
        }
      }
    } catch {
      // Fallback to the raw user query.
    }

    return {
      subqueries,
      search_results: [],
      pending_urls: [],
      visited_urls: [],
      notes: [],
      search_round: 0,
      reflection: "",
      action: "",
    };
  }

  private async doSearchWeb(state: AgentState): Promise<Partial<AgentState>> {
    const seen = new Set([...state.pending_urls, ...state.visited_urls]);
    const allResults = [...state.search_results];

    for (const query of state.subqueries) {
      const rows = await searchWeb(query, 6);
      for (const row of rows) {
        const url = row.url;
        if (!url || seen.has(url)) {
          continue;
        }
        seen.add(url);
        allResults.push(row);
      }
    }

    return {
      search_results: allResults,
      pending_urls: allResults.map((item) => item.url).filter(Boolean),
      search_round: state.search_round + 1,
    };
  }

  private pickNextUrl(state: AgentState): Partial<AgentState> {
    const visited = new Set(state.visited_urls);
    if (visited.size >= state.max_pages) {
      return { current_url: "", visited_urls: [...visited] };
    }

    for (const url of state.pending_urls) {
      if (!visited.has(url)) {
        visited.add(url);
        return { current_url: url, visited_urls: [...visited] };
      }
    }

    return { current_url: "", visited_urls: [...visited] };
  }

  private async exploreDomLinks(baseUrl: string, notes: DeepSearchNote[]): Promise<DeepSearchNote[]> {
    await this.initBrowser();
    const page = this.page;
    if (!page) {
      return notes;
    }

    await page.goto(baseUrl, { timeout: 15_000, waitUntil: "domcontentloaded" });
    const candidateLinks = page.locator("main a, article a, a");
    const count = Math.min(await candidateLinks.count(), 4);

    for (let i = 0; i < count; i += 1) {
      try {
        await page.goto(baseUrl, { timeout: 15_000, waitUntil: "domcontentloaded" });
        const link = page.locator("main a, article a, a").nth(i);
        const text = (await link.innerText()).trim();
        if (text.length < 2) {
          continue;
        }

        await link.scrollIntoViewIfNeeded({ timeout: 2_000 });
        const oldUrl = page.url();
        await link.click({ timeout: 4_000 });
        await page.waitForLoadState("domcontentloaded", { timeout: 8_000 });
        const newUrl = page.url();
        if (newUrl === oldUrl) {
          continue;
        }

        const innerText = (await page.locator("body").innerText()).trim();
        if (innerText.length < 300) {
          continue;
        }

        notes.push({
          url: newUrl,
          title: (await page.title()).trim() || `DOM点击: ${text}`,
          content: innerText.slice(0, 3000),
        });
        break;
      } catch {
        // Try next link.
      }
    }

    return notes;
  }

  private async readPage(state: AgentState): Promise<Partial<AgentState>> {
    const url = state.current_url;
    let notes = [...state.notes];
    if (!url) {
      return { notes };
    }

    try {
      await this.initBrowser();
      const page = this.page;
      if (!page) {
        throw new Error("browser page not initialized");
      }
      await page.goto(url, { timeout: 15_000, waitUntil: "domcontentloaded" });
      const title = await page.title();
      const text = (await page.locator("body").innerText()).trim();
      notes.push({ url, title: title.trim(), content: text.slice(0, 3500) });

      if (text.length < 300) {
        notes = await this.exploreDomLinks(url, notes);
      }
    } catch (error) {
      if (error instanceof errors.TimeoutError) {
        notes.push({ url, title: "读取超时", content: `页面读取超时: ${error.message}` });
      } else {
        notes.push({
          url,
          title: "读取失败",
          content: `无法读取页面内容: ${error instanceof Error ? error.message : String(error)}`,
        });
      }
    }

    return { notes };
  }

  private async reflect(state: AgentState): Promise<Partial<AgentState>> {
    const notes = state.notes;
    if (notes.length === 0 && state.search_round < state.max_rounds) {
      return { action: "search_web", reflection: "没有拿到可用内容，触发二次搜索。" };
    }

    if (notes.length > 0) {
      const lastNote = notes.at(-1);
      const content = lastNote?.content ?? "";
      const failed = ["失败", "超时"].some((word) => (lastNote?.title ?? "").includes(word));
      const shallow = content.trim().length < 180;

      if ((failed || shallow) && state.search_round < state.max_rounds) {
        const refinedQuery = await this.generateRefinedQuery(state);
        const subqueries = refinedQuery ? uniqueTail([...state.subqueries, refinedQuery], 6) : state.subqueries;
        return {
          action: "search_web",
          subqueries,
          reflection: `内容质量不足，新增检索词: ${refinedQuery}`,
        };
      }
    }

    const visited = new Set(state.visited_urls);
    const hasUnvisited = state.pending_urls.some((url) => !visited.has(url));
    if (hasUnvisited && visited.size < state.max_pages) {
      return { action: "pick_next_url", reflection: "继续访问下一条结果。" };
    }

    return { action: "summarize", reflection: "信息已足够，进入总结。" };
  }

  private async generateRefinedQuery(state: AgentState): Promise<string> {
    const noteText = state.notes
      .slice(-2)
      .map((note) => `${note.title} ${note.content.slice(0, 120)}`)
      .join("\n");

    try {
      const response = await this.client.chat.completions.create({
        model: this.models.router,
        messages: [
          {
            role: "system",
            content: "你是搜索反思器。根据失败线索给出一个更精准的新检索词，只输出一行纯文本。",
          },
          {
            role: "user",
            content: `原问题: ${state.query}\n失败线索: ${noteText}\n请输出新检索词。`,
          },
        ],
        temperature: 0.3,
        stream: false,
      });
      const query = String(response.choices?.[0]?.message?.content ?? "").trim();
      return query || `${state.query} 官方文档 详细说明`;
    } catch {
      return `${state.query} 官方文档 详细说明`;
    }
  }

  private async summarize(state: AgentState): Promise<Partial<AgentState>> {
    if (state.notes.length === 0) {
      return { final_answer: "未检索到可用信息，请尝试更具体的问题。" };
    }

    const sources = state.notes
      .map((note) => `来源: ${note.url}\n标题: ${note.title}\n内容摘要: ${note.content}`)
      .join("\n\n");

    let answer = "";
    try {
      const response = await this.client.chat.completions.create({
        model: this.models.summary,
        messages: [
          {
            role: "system",
            content: "你是研究助理。请基于给定来源总结答案，先给结论，再列出要点，并附上来源链接。",
          },
          { role: "user", content: `问题：${state.query}\n\n请基于以下资料回答：\n${sources}` },
        ],
        temperature: 0.2,
        stream: false,
      });
      answer = String(response.choices?.[0]?.message?.content ?? "");
    } catch {
      answer = `检索完成（LLM总结失败），可参考以下来源：\n${state.notes
        .map((note) => `- ${note.title}: ${note.url}`)
        .join("\n")}`;
    }

    if (state.reflection) {
      answer = `[检索反思] ${state.reflection}\n\n${answer}`;
    }
    return { final_answer: answer };
  }

  async arun(query: string, maxPages = 5, maxRounds = 3): Promise<string> {
    let state: AgentState = {
      query,
      subqueries: [query],
      search_results: [],
      pending_urls: [],
      current_url: "",
      visited_urls: [],
      notes: [],
      max_pages: maxPages,
      max_rounds: maxRounds,
      search_round: 0,
      reflection: "",
      action: "",
      final_answer: "",
    };

    try {
      state = { ...state, ...(await this.planQueries(state)) };
      state = { ...state, ...(await this.doSearchWeb(state)) };

      for (let step = 0; step < 64; step += 1) {
        state = { ...state, ...this.pickNextUrl(state) };
        if (state.current_url) {
          state = { ...state, ...(await this.readPage(state)) };
        }

        state = { ...state, ...(await this.reflect(state)) };
        if (state.action === "search_web") {
          state = { ...state, ...(await this.doSearchWeb(state)) };
          continue;
        }
        if (state.action === "pick_next_url") {
          continue;
        }
        state = { ...state, ...(await this.summarize(state)) };
        return state.final_answer;
      }

      state = { ...state, ...(await this.summarize(state)) };
      return state.final_answer;
    } finally {
      await this.closeBrowser();
    }
  }

  async run(query: string, maxPages = 5, maxRounds = 3): Promise<string> {
    return this.arun(query, maxPages, maxRounds);
  }
}
