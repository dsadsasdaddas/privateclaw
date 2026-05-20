import fs from "node:fs";
import path from "node:path";
import type { ChatHistoryItem } from "./types.js";
import { loadModels } from "./config.js";

function nowDay(): string {
  return new Date().toISOString().slice(0, 10);
}

function nowMinuteLocal(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function readText(pathname: string): string {
  return fs.existsSync(pathname) ? fs.readFileSync(pathname, "utf8").trim() : "";
}

export class MemoryStore {
  readonly memoryMd: string;
  readonly dailyDir: string;

  constructor(readonly rootDir: string) {
    this.memoryMd = path.join(rootDir, "MEMORY.md");
    this.dailyDir = path.join(rootDir, "memory");
  }

  ensureMdFiles(): void {
    fs.mkdirSync(this.rootDir, { recursive: true });
    fs.mkdirSync(this.dailyDir, { recursive: true });

    if (!fs.existsSync(this.memoryMd)) {
      fs.writeFileSync(
        this.memoryMd,
        [
          "# MEMORY",
          "",
          "## 长期稳定信息（偏好/规则/身份/项目约定）",
          "- 你是一个可靠、务实、尊重用户意图的 AI 助手。",
          "",
          "## 稳定偏好提炼",
          "- 暂无。",
          "",
          "## 归档摘要",
          "- 暂无。",
          "",
        ].join("\n"),
        "utf8",
      );
    }

    const todayPath = this.getDailyFilePath();
    if (!fs.existsSync(todayPath)) {
      fs.writeFileSync(
        todayPath,
        [`# ${nowDay()} 工作记忆`, "", "## 今天做了什么", "", "## 临时决定", "", "## 正在排查的问题", "", "## 对话记录", ""].join("\n"),
        "utf8",
      );
    }
  }

  readMemory(): string {
    return readText(this.memoryMd);
  }

  writeMemory(memoryText: string): void {
    fs.writeFileSync(this.memoryMd, memoryText, "utf8");
  }

  getDailyFilePath(day = new Date()): string {
    const y = day.getFullYear();
    const m = String(day.getMonth() + 1).padStart(2, "0");
    const d = String(day.getDate()).padStart(2, "0");
    return path.join(this.dailyDir, `${y}-${m}-${d}.md`);
  }

  appendDailyDialogue(userInput: string, assistantOutput: string): void {
    const dailyPath = this.getDailyFilePath();
    if (!fs.existsSync(dailyPath)) {
      this.ensureMdFiles();
    }
    const safeUser = userInput.replaceAll("\n", " ").trim();
    const safeAssistant = assistantOutput.replaceAll("\n", " ").trim();
    const line = `- [${nowMinuteLocal()}] U: ${safeUser} | A: ${safeAssistant.slice(0, 300)}\n`;
    fs.appendFileSync(dailyPath, line, "utf8");
  }

  readRecentDailyLines(limit = 40): string[] {
    if (!fs.existsSync(this.dailyDir)) {
      return [];
    }
    const collected: string[] = [];
    const files = fs
      .readdirSync(this.dailyDir)
      .filter((name) => name.endsWith(".md"))
      .sort()
      .reverse();

    for (const file of files) {
      const text = fs.readFileSync(path.join(this.dailyDir, file), "utf8");
      const lines = text.split(/\r?\n/).filter((line) => line.trim().startsWith("- ["));
      collected.push(...lines.reverse());
      if (collected.length >= limit) {
        break;
      }
    }
    return collected.slice(0, limit);
  }

  appendMemorySection(sectionTitle: string, content: string): void {
    let memoryText = this.readMemory();
    const marker = `## ${sectionTitle}`;
    if (!memoryText.includes(marker)) {
      memoryText = `${memoryText.trim()}\n\n${marker}\n`;
    }
    memoryText = `${memoryText.trim()}\n- ${content.trim()}\n`;
    this.writeMemory(memoryText);
  }
}

export class MemoryRefiner {
  private readonly routerModel: string;

  constructor(
    private readonly client: any,
    private readonly store: MemoryStore,
    private readonly maxRecentLines = 40,
    projectRoot = process.cwd(),
  ) {
    this.routerModel = loadModels(projectRoot).router;
  }

  async updateMemory(userInput: string, assistantOutput: string): Promise<void> {
    this.store.appendDailyDialogue(userInput, assistantOutput);

    const recentLines = this.store.readRecentDailyLines(this.maxRecentLines + 8);
    if (recentLines.length > this.maxRecentLines) {
      const cut = Math.floor(recentLines.length / 2);
      const compressed = await this.compressWithLlm(recentLines.slice(0, cut));
      this.store.appendMemorySection("归档摘要", `${nowDay()} 压缩记忆: ${compressed}`);
    }
  }

  async maybeUpdateSoul(): Promise<void> {
    const recentLines = this.store.readRecentDailyLines(24);
    if (recentLines.length === 0 || recentLines.length % 8 !== 0) {
      return;
    }

    const memoryText = this.store.readMemory();
    const recentContext = recentLines.slice(-12).join("\n");
    try {
      const response = await this.client.chat.completions.create({
        model: this.routerModel,
        messages: [
          {
            role: "system",
            content: "你是长期偏好提炼器。根据最近对话提炼稳定偏好/规则/项目约定，输出 3-6 条短句。",
          },
          {
            role: "user",
            content: `当前 MEMORY:\n${memoryText}\n\n最近对话:\n${recentContext}`,
          },
        ],
        temperature: 0.2,
        stream: false,
      });
      const updated = String(response.choices?.[0]?.message?.content ?? "").trim();
      if (updated) {
        this.store.appendMemorySection("稳定偏好提炼", updated);
      }
    } catch {
      // Memory refinement is best-effort and must not fail the user request.
    }
  }

  async compactHistoryIfNeeded(historyList: ChatHistoryItem[], maxChars = 12_000): Promise<ChatHistoryItem[]> {
    const totalChars = historyList.reduce((sum, msg) => {
      const content = typeof msg.content === "string" ? msg.content : "";
      return sum + content.length;
    }, 0);
    if (totalChars < maxChars || historyList.length < 12) {
      return historyList;
    }

    const cut = Math.floor(historyList.length * 0.7);
    const oldChunk = historyList.slice(0, cut);
    const keepChunk = historyList.slice(cut);

    const compactSource = oldChunk.map((msg) => {
      const role = typeof msg.role === "string" ? msg.role : "";
      const content = typeof msg.content === "string" ? msg.content.replaceAll("\n", " ") : "";
      return `${role}: ${content.slice(0, 300)}`;
    });

    const summary = await this.compressWithLlm(compactSource);
    await this.updateMemory("系统自动压缩上下文", summary);
    return keepChunk;
  }

  private async compressWithLlm(lines: string[]): Promise<string> {
    const joined = lines.join("\n");
    try {
      const response = await this.client.chat.completions.create({
        model: this.routerModel,
        messages: [
          {
            role: "system",
            content: "你是记忆压缩器。请把对话压缩成 3-6 条可复用记忆，聚焦偏好、目标、约束。",
          },
          { role: "user", content: joined },
        ],
        temperature: 0.2,
        stream: false,
      });
      return String(response.choices?.[0]?.message?.content ?? "").trim();
    } catch {
      const preview = lines.slice(0, 3).join(" | ");
      return `- 历史对话压缩（fallback）: ${preview.slice(0, 200)}`;
    }
  }
}

export class ContextAssembler {
  constructor(private readonly store: MemoryStore) {}

  buildSystemContext(): string {
    const memory = this.store.readMemory();
    const dailyLines = this.store.readRecentDailyLines(20);
    const recentDaily = dailyLines.length > 0 ? dailyLines.slice(-12).join("\n") : "- 暂无今日记录";
    const recentPreview = memory.length > 2400 ? memory.slice(-2400) : memory;
    return `请严格遵循以下长期上下文（MEMORY.md）与最近工作日志（memory/YYYY-MM-DD.md）：\n\n${recentPreview}\n\n## 最近工作日志片段\n${recentDaily}`;
  }
}

export class MemoryContextManager {
  private readonly memoryScopesDir: string;
  private readonly stores = new Map<string, MemoryStore>();
  private readonly refiners = new Map<string, MemoryRefiner>();
  private readonly assemblers = new Map<string, ContextAssembler>();

  constructor(
    private readonly client: any,
    private readonly rootDir: string,
    private readonly maxRecentLines = 40,
  ) {
    this.memoryScopesDir = path.join(rootDir, "memory_scopes");
  }

  private sanitizeScopeId(userScopeId?: string): string {
    const raw = (userScopeId ?? "default").trim() || "default";
    return raw.replace(/[^a-zA-Z0-9._-]/g, "_");
  }

  private ensureScopeComponents(userScopeId?: string): string {
    const scope = this.sanitizeScopeId(userScopeId);
    if (!this.stores.has(scope)) {
      const scopeDir = path.join(this.memoryScopesDir, scope);
      const store = new MemoryStore(scopeDir);
      const refiner = new MemoryRefiner(this.client, store, this.maxRecentLines, this.rootDir);
      const assembler = new ContextAssembler(store);
      this.stores.set(scope, store);
      this.refiners.set(scope, refiner);
      this.assemblers.set(scope, assembler);
    }
    return scope;
  }

  ensureMdFiles(userScopeId?: string): void {
    const scope = this.ensureScopeComponents(userScopeId);
    this.stores.get(scope)?.ensureMdFiles();
  }

  buildSystemContext(userScopeId?: string): string {
    const scope = this.ensureScopeComponents(userScopeId);
    this.stores.get(scope)?.ensureMdFiles();
    return this.assemblers.get(scope)?.buildSystemContext() ?? "";
  }

  async updateMemory(userInput: string, assistantOutput: string, userScopeId?: string): Promise<void> {
    const scope = this.ensureScopeComponents(userScopeId);
    this.stores.get(scope)?.ensureMdFiles();
    await this.refiners.get(scope)?.updateMemory(userInput, assistantOutput);
  }

  async maybeUpdateSoul(userScopeId?: string): Promise<void> {
    const scope = this.ensureScopeComponents(userScopeId);
    this.stores.get(scope)?.ensureMdFiles();
    await this.refiners.get(scope)?.maybeUpdateSoul();
  }

  async compactHistoryIfNeeded(
    historyList: ChatHistoryItem[],
    maxChars = 12_000,
    userScopeId?: string,
  ): Promise<ChatHistoryItem[]> {
    const scope = this.ensureScopeComponents(userScopeId);
    this.stores.get(scope)?.ensureMdFiles();
    return (await this.refiners.get(scope)?.compactHistoryIfNeeded(historyList, maxChars)) ?? historyList;
  }
}
