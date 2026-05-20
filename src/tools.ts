import fs from "node:fs";
import path from "node:path";
import { exec, execFile } from "node:child_process";
import { promisify } from "node:util";
import type { AvailableTools, SearchResult } from "./types.js";
import { setAlarm } from "./skills/set-alarm.js";

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);

export function getSystemTime(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

function decodeHtmlEntities(text: string): string {
  return text
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replace(/&#(\d+);/g, (_m, n: string) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_m, n: string) => String.fromCodePoint(Number.parseInt(n, 16)));
}

function stripHtml(html: string): string {
  return decodeHtmlEntities(html.replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " "))
    .replace(/\s+/g, " ")
    .trim();
}

function resolveWorkspacePath(filePath: string): string {
  const clean = filePath.trim();
  if (!clean) {
    throw new Error("path 不能为空");
  }
  return path.isAbsolute(clean) ? clean : path.join(process.cwd(), clean);
}

function normalizeDuckDuckGoUrl(href: string): string {
  let url = decodeHtmlEntities(href.trim());
  if (url.startsWith("//")) {
    url = `https:${url}`;
  }
  try {
    const parsed = new URL(url);
    const redirected = parsed.searchParams.get("uddg");
    if (redirected) {
      return decodeURIComponent(redirected);
    }
  } catch {
    // Keep original string if URL parsing fails.
  }
  return url;
}

export async function searchWeb(query: string, maxResults = 5): Promise<SearchResult[]> {
  const q = query.trim();
  if (!q) {
    return [];
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);
  try {
    const response = await fetch(`https://duckduckgo.com/html/?q=${encodeURIComponent(q)}`, {
      headers: {
        "user-agent": "Mozilla/5.0 AIGC-CLI/0.1 (+https://duckduckgo.com)",
      },
      signal: controller.signal,
    });
    const html = await response.text();

    const results: SearchResult[] = [];
    const blockRegex = /<div[^>]+class="[^"]*result[^"]*"[\s\S]*?(?=<div[^>]+class="[^"]*result[^"]*"|<\/body>)/gi;
    const blocks = html.match(blockRegex) ?? [];

    for (const block of blocks) {
      const anchorMatch = block.match(/<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/i);
      if (!anchorMatch?.[1]) {
        continue;
      }
      const url = normalizeDuckDuckGoUrl(anchorMatch[1]);
      if (!/^https?:\/\//i.test(url)) {
        continue;
      }
      const title = stripHtml(anchorMatch[2] ?? "");
      const snippetMatch = block.match(/<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)<\/a>/i) ?? block.match(/<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)<\/div>/i);
      const snippet = stripHtml(snippetMatch?.[1] ?? "");
      if (title || snippet) {
        results.push({ title, snippet, url });
      }
      if (results.length >= maxResults) {
        break;
      }
    }

    return results;
  } catch (error) {
    return [
      {
        title: "search failed",
        snippet: error instanceof Error ? error.message : String(error),
        url: "",
      },
    ];
  } finally {
    clearTimeout(timeout);
  }
}

export async function readUrl(url: string): Promise<string> {
  const target = url.trim();
  if (!/^https?:\/\//i.test(target)) {
    return "read_url 参数 url 必须是 http(s) URL。";
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(target, {
      headers: {
        "user-agent": "Mozilla/5.0 AIGC-CLI/0.1",
      },
      signal: controller.signal,
    });
    const contentType = response.headers.get("content-type") ?? "";
    const text = await response.text();
    const body = contentType.includes("html") ? stripHtml(text) : text;
    return `url: ${target}\nstatus: ${response.status}\n\n${body.slice(0, 8000)}`;
  } catch (error) {
    return `read_url failed: ${error instanceof Error ? error.message : String(error)}`;
  } finally {
    clearTimeout(timeout);
  }
}

export function readFileTool(filePath: string): string {
  try {
    const absolutePath = resolveWorkspacePath(filePath);
    const stat = fs.statSync(absolutePath);
    if (!stat.isFile()) {
      return `read_file failed: ${filePath} is not a file`;
    }
    const content = fs.readFileSync(absolutePath, "utf8");
    return content.length > 120_000 ? `${content.slice(0, 120_000)}\n\n[truncated ${content.length - 120_000} chars]` : content;
  } catch (error) {
    return `read_file failed: ${error instanceof Error ? error.message : String(error)}`;
  }
}

export function writeFileTool(filePath: string, content: string): string {
  try {
    const absolutePath = resolveWorkspacePath(filePath);
    fs.mkdirSync(path.dirname(absolutePath), { recursive: true });
    fs.writeFileSync(absolutePath, content, "utf8");
    return `write_file ok: ${filePath}`;
  } catch (error) {
    return `write_file failed: ${error instanceof Error ? error.message : String(error)}`;
  }
}

export function listFilesTool(dirPath: string, maxDepth = 2): string {
  try {
    const root = resolveWorkspacePath(dirPath || ".");
    const results: string[] = [];
    const walk = (current: string, depth: number) => {
      if (depth > maxDepth) {
        return;
      }
      for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
        if (entry.name === "node_modules" || entry.name === ".git" || entry.name === "dist") {
          continue;
        }
        const full = path.join(current, entry.name);
        const rel = path.relative(process.cwd(), full) || ".";
        results.push(entry.isDirectory() ? `${rel}/` : rel);
        if (entry.isDirectory()) {
          walk(full, depth + 1);
        }
      }
    };
    walk(root, 0);
    return results.slice(0, 500).join("\n") || "(empty)";
  } catch (error) {
    return `list_files failed: ${error instanceof Error ? error.message : String(error)}`;
  }
}

export async function executePythonCode(codeString: string): Promise<string> {
  const python = process.env.PYTHON ?? "python3";
  try {
    const { stdout, stderr } = await execFileAsync(python, ["-c", codeString], {
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
      encoding: "utf8",
    });
    return [`Executing code:\n${codeString}`, stdout.trim(), stderr.trim() ? `stderr:\n${stderr.trim()}` : ""]
      .filter(Boolean)
      .join("\n");
  } catch (error: any) {
    const stdout = typeof error?.stdout === "string" ? error.stdout.trim() : "";
    const stderr = typeof error?.stderr === "string" ? error.stderr.trim() : "";
    const details = [stdout && `stdout:\n${stdout}`, stderr && `stderr:\n${stderr}`].filter(Boolean).join("\n");
    return `Error executing code:\n${error?.message ?? String(error)}${details ? `\n${details}` : ""}`;
  }
}

export function createNewSkills(args: {
  skill_name?: unknown;
  python_code?: unknown;
  typescript_code?: unknown;
  yaml_config?: unknown;
}): string {
  const skillName = String(args.skill_name ?? "").trim();
  if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(skillName)) {
    return "skill_name 必须是合法的英文函数名。";
  }

  const rootDir = process.cwd();
  const yamlConfig = String(args.yaml_config ?? "").trim();
  const typeScriptCode = String(args.typescript_code ?? "").trim();
  const pythonCode = String(args.python_code ?? "").trim();

  if (!typeScriptCode && !pythonCode) {
    return "必须提供 typescript_code 或 python_code。";
  }

  if (typeScriptCode) {
    const skillDir = path.join(rootDir, "src", "skills");
    fs.mkdirSync(skillDir, { recursive: true });
    fs.writeFileSync(path.join(skillDir, `${skillName}.ts`), typeScriptCode, "utf8");
  } else {
    const legacySkillDir = path.join(rootDir, "skills");
    fs.mkdirSync(legacySkillDir, { recursive: true });
    fs.writeFileSync(path.join(legacySkillDir, `${skillName}.py`), pythonCode, "utf8");
  }

  if (yamlConfig) {
    fs.appendFileSync(path.join(rootDir, "dynamic_config.yaml"), `\n${yamlConfig}\n`, "utf8");
  }

  return `Skill '${skillName}' created successfully.`;
}

const DANGEROUS_COMMANDS = new Set([
  "rm",
  "reboot",
  "shutdown",
  "poweroff",
  "halt",
  "mkfs",
  "dd",
  "fdisk",
  "killall",
  "chown",
]);

const scheduledTasks = new Map<
  string,
  {
    status: "scheduled" | "done";
    delay_seconds: number;
    command: string;
    result: string;
  }
>();

export function isDangerousCommand(command: string): boolean {
  const trimmed = command.trim();
  if (!trimmed) {
    return true;
  }

  const first = trimmed.match(/^\s*(?:sudo\s+)?([^\s;&|]+)/)?.[1]?.toLowerCase() ?? "";
  if (DANGEROUS_COMMANDS.has(first)) {
    return true;
  }

  const normalized = ` ${trimmed.toLowerCase()} `;
  return [" rm ", " rm-", "sudo rm", "mkfs", "shutdown", "reboot", "poweroff", ":(){:|:&};:"].some((pattern) =>
    normalized.includes(pattern),
  );
}

export async function execCliCommand(command: string): Promise<string> {
  if (isDangerousCommand(command)) {
    return `命令被拒绝：检测到危险命令或空命令。command=${command}`;
  }

  try {
    const { stdout, stderr } = await execAsync(command, {
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
      encoding: "utf8",
    });
    const out = stdout.trim();
    const err = stderr.trim();
    return [out || "命令执行成功（无输出）。", err && `stderr:\n${err}`].filter(Boolean).join("\n");
  } catch (error: any) {
    const stdout = typeof error?.stdout === "string" ? error.stdout.trim() : "";
    const stderr = typeof error?.stderr === "string" ? error.stderr.trim() : "";
    const code = error?.code ?? "unknown";
    return `命令执行失败（code=${code})\nstdout:\n${stdout}\nstderr:\n${stderr || error?.message || String(error)}`;
  }
}

export function scheduleCliCommand(delaySeconds: number, command: string): string {
  if (!Number.isFinite(delaySeconds) || delaySeconds <= 0) {
    return "delay_seconds 必须大于 0。";
  }
  if (delaySeconds > 86_400) {
    return "delay_seconds 过大，当前仅支持 86400 秒内任务。";
  }
  if (isDangerousCommand(command)) {
    return `命令被拒绝：检测到危险命令或空命令。command=${command}`;
  }

  const taskId = Math.random().toString(16).slice(2, 10);
  scheduledTasks.set(taskId, {
    status: "scheduled",
    delay_seconds: delaySeconds,
    command,
    result: "",
  });

  setTimeout(() => {
    void execCliCommand(command).then((result) => {
      const task = scheduledTasks.get(taskId);
      if (task) {
        task.status = "done";
        task.result = result;
      }
      console.log(`[SCHEDULE][${taskId}] command done: ${command} -> ${String(result).slice(0, 180)}`);
    });
  }, delaySeconds * 1000).unref?.();

  return `定时任务已创建，task_id=${taskId}，将在 ${delaySeconds} 秒后执行：${command}`;
}

export function buildAvailableTools(deepSearchAgent?: { run: (query: string) => Promise<string> }): AvailableTools {
  return {
    get_system_time: () => getSystemTime(),
    web_search: async (args) => searchWeb(String(args.query ?? ""), 5),
    deep_search: async (args) => {
      if (!deepSearchAgent) {
        return "deep_search 不可用：DeepSearch agent 尚未初始化。";
      }
      const query = String(args.query ?? "").trim();
      if (!query) {
        return "deep_search 参数 query 不能为空。";
      }
      return deepSearchAgent.run(query);
    },
    execute_python_code: async (args) => executePythonCode(String(args.code_string ?? "")),
    create_new_skills: (args) => createNewSkills(args),
    exec_cli_command: async (args) => execCliCommand(String(args.command ?? "")),
    schedule_cli_command: (args) => scheduleCliCommand(Number(args.delay_seconds), String(args.command ?? "")),
    set_alarm: (args) => setAlarm(String(args.alarm_time_str ?? ""), String(args.message ?? "")),
    read_url: async (args) => readUrl(String(args.url ?? "")),
    read_file: (args) => readFileTool(String(args.path ?? args.file_path ?? "")),
    write_file: (args) => writeFileTool(String(args.path ?? args.file_path ?? ""), String(args.content ?? "")),
    list_files: (args) => listFilesTool(String(args.path ?? "."), Number(args.max_depth ?? 2)),
  };
}
