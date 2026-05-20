import fs from "node:fs";
import path from "node:path";
import YAML from "yaml";
import type { Personalization, PersonalizationModels, ToolConfig } from "./types.js";

export const DEFAULT_PERSONALIZATION: Personalization = {
  api_key_env: "DASHSCOPE_API_KEY",
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  models: {
    chat: "qwen-max",
    router: "qwen-max",
    fsm: "qwen-max",
    plan: "qwen-max",
    summary: "qwen-max",
  },
};

export function projectPath(rootDir: string, ...segments: string[]): string {
  return path.join(rootDir, ...segments);
}

export function readYamlFile<T>(filePath: string, fallback: T): T {
  try {
    if (!fs.existsSync(filePath)) {
      return fallback;
    }
    const raw = fs.readFileSync(filePath, "utf8");
    const parsed = YAML.parse(raw);
    return (parsed ?? fallback) as T;
  } catch {
    return fallback;
  }
}

export function loadPersonalization(rootDir = process.cwd()): Personalization {
  const raw = readYamlFile<Record<string, unknown>>(projectPath(rootDir, "personalization.yaml"), {});
  const defaults = structuredClone(DEFAULT_PERSONALIZATION);

  if (typeof raw.api_key_env === "string" && raw.api_key_env.trim()) {
    defaults.api_key_env = raw.api_key_env.trim();
  }
  if (typeof raw.base_url === "string" && raw.base_url.trim()) {
    defaults.base_url = raw.base_url.trim();
  }
  if (typeof raw.deepsearch_trigger_keyword === "string" && raw.deepsearch_trigger_keyword.trim()) {
    defaults.deepsearch_trigger_keyword = raw.deepsearch_trigger_keyword.trim();
  }

  const rawModels = typeof raw.models === "object" && raw.models !== null ? (raw.models as Record<string, unknown>) : {};
  for (const key of Object.keys(defaults.models) as (keyof PersonalizationModels)[]) {
    const value = rawModels[key];
    if (typeof value === "string" && value.trim()) {
      defaults.models[key] = value.trim();
    }
  }

  return defaults;
}

export function loadModels(rootDir = process.cwd()): Pick<PersonalizationModels, "plan" | "router" | "summary"> {
  const personalization = loadPersonalization(rootDir);
  return {
    plan: personalization.models.plan,
    router: personalization.models.router,
    summary: personalization.models.summary,
  };
}

function normalizeToolConfig(raw: unknown): ToolConfig {
  if (Array.isArray(raw)) {
    return raw.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null);
  }
  if (typeof raw === "object" && raw !== null) {
    return [raw as Record<string, unknown>];
  }
  return [];
}

export function loadToolConfig(rootDir = process.cwd()): ToolConfig {
  const coreTools = normalizeToolConfig(readYamlFile<unknown>(projectPath(rootDir, "tool_config.yaml"), []));
  const dynamicTools = normalizeToolConfig(readYamlFile<unknown>(projectPath(rootDir, "dynamic_config.yaml"), []));
  return [...coreTools, ...dynamicTools];
}
