import { deriveChildContext, type ResourceScope, type ToolSpec } from "./capabilities.js";
import { AgentProfiles } from "./profiles.js";
import type { AgentLoop } from "./agent-loop.js";

const SPAWNABLE_PROFILES = ["researcher", "coder", "tester"] as const;
type SpawnableProfile = (typeof SPAWNABLE_PROFILES)[number];

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const cleaned = value.map((item) => String(item).trim()).filter(Boolean);
  return cleaned.length > 0 ? cleaned : undefined;
}

function normalizeScope(value: unknown): Partial<ResourceScope> | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const raw = value as Record<string, unknown>;
  const scope: Partial<ResourceScope> = {};
  const readPaths = stringArray(raw.readPaths ?? raw.read_paths);
  const writePaths = stringArray(raw.writePaths ?? raw.write_paths);
  const deniedPaths = stringArray(raw.deniedPaths ?? raw.denied_paths);
  const commands = stringArray(raw.commands);
  const network = stringArray(raw.network);
  const envKeys = stringArray(raw.envKeys ?? raw.env_keys);

  if (readPaths) scope.readPaths = readPaths;
  if (writePaths) scope.writePaths = writePaths;
  if (deniedPaths) scope.deniedPaths = deniedPaths;
  if (commands) scope.commands = commands;
  if (network) scope.network = network;
  if (envKeys) scope.envKeys = envKeys;
  return Object.keys(scope).length > 0 ? scope : undefined;
}

function isSpawnableProfile(value: string): value is SpawnableProfile {
  return (SPAWNABLE_PROFILES as readonly string[]).includes(value);
}

export function createSpawnAgentTool(agentLoop: AgentLoop): ToolSpec {
  return {
    name: "spawn_agent",
    requiredCapabilities: ["agent:spawn"],
    resourceKind: "none",
    schema: {
      type: "function",
      function: {
        name: "spawn_agent",
        description:
          "Spawn a constrained subagent using the same AgentLoop. Use researcher for web research, coder for authorized file edits, tester for whitelisted checks. Child permissions are the intersection of parent delegate grants, child profile grants, and requested scope.",
        parameters: {
          type: "object",
          properties: {
            profile: {
              type: "string",
              enum: [...SPAWNABLE_PROFILES],
              description: "Subagent profile to run.",
            },
            task: {
              type: "string",
              description: "Concrete, self-contained task for the subagent.",
            },
            scope: {
              type: "object",
              description: "Optional narrowing scope. It can only reduce permissions, never expand them.",
              properties: {
                readPaths: { type: "array", items: { type: "string" } },
                writePaths: { type: "array", items: { type: "string" } },
                deniedPaths: { type: "array", items: { type: "string" } },
                commands: { type: "array", items: { type: "string" } },
                network: { type: "array", items: { type: "string" } },
              },
            },
          },
          required: ["profile", "task"],
        },
      },
    },
    run: async (args, parentCtx) => {
      const profileName = String(args.profile ?? "").trim();
      const task = String(args.task ?? "").trim();
      if (!isSpawnableProfile(profileName)) {
        return `spawn_agent denied: unsupported profile '${profileName}'. allowed=${SPAWNABLE_PROFILES.join(",")}`;
      }
      if (!task) {
        return "spawn_agent denied: task is required.";
      }

      try {
        const requestedScope = normalizeScope(args.scope);
        const deriveArgs: Parameters<typeof deriveChildContext>[0] = {
          parent: parentCtx,
          childProfile: AgentProfiles[profileName],
        };
        if (requestedScope) {
          deriveArgs.requestedScope = requestedScope;
        }
        const childCtx = deriveChildContext(deriveArgs);
        const text = await agentLoop.runSubagentTask(task, childCtx);
        return JSON.stringify(
          {
            profile: profileName,
            agentId: childCtx.agentId,
            parentAgentId: childCtx.parentAgentId,
            taskId: childCtx.taskId,
            depth: childCtx.depth,
            effectiveTools: childCtx.effectiveGrants.tools,
            result: text,
          },
          null,
          2,
        );
      } catch (error) {
        return `spawn_agent failed: ${error instanceof Error ? error.message : String(error)}`;
      }
    },
  };
}
