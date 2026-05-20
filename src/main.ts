import OpenAI from "openai";
import { loadPersonalization, loadToolConfig } from "./config.js";
import { MemoryContextManager } from "./context-memory.js";
import { DeepSearch } from "./deepsearch.js";
import { buildAvailableTools } from "./tools.js";
import { CapabilityBroker, InMemoryAuditLogger } from "./capabilities.js";
import { createAssistantProfile } from "./profiles.js";
import { buildToolRegistry } from "./tool-registry.js";
import { AgentLoop } from "./agent-loop.js";
import { AgentRuntime } from "./agent-runtime.js";
import { FeishuEntry } from "./feishu-entry.js";
import type { Personalization } from "./types.js";

function buildClient(personalization: Personalization): OpenAI {
  const apiKeyEnv = personalization.api_key_env;
  const apiKey = (process.env[apiKeyEnv] ?? "").trim();
  if (!apiKey) {
    throw new Error(`Missing ${apiKeyEnv}. Please set it in environment variables.`);
  }
  return new OpenAI({ apiKey, baseURL: personalization.base_url });
}

export async function main(): Promise<void> {
  const rootDir = process.cwd();
  const personalization = loadPersonalization(rootDir);
  const client = buildClient(personalization);

  const memoryManager = new MemoryContextManager(client, rootDir);
  memoryManager.ensureMdFiles();

  const toolConfig = loadToolConfig(rootDir);
  const deepSearchAgent = new DeepSearch(client, rootDir);
  const availableTools = buildAvailableTools(deepSearchAgent);
  const toolRegistry = buildToolRegistry(toolConfig, availableTools);
  const audit = new InMemoryAuditLogger();
  const broker = new CapabilityBroker(toolRegistry, audit);
  const rootProfile = createAssistantProfile(personalization);

  const agentLoop = new AgentLoop(client, memoryManager, broker, rootProfile, personalization);
  const runtime = new AgentRuntime(agentLoop);

  const messageEntry = (process.env.MESSAGE_ENTRY ?? "feishu").trim().toLowerCase();
  if (messageEntry === "cli") {
    await runtime.run();
    return;
  }

  await new FeishuEntry(runtime).run();
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
});
