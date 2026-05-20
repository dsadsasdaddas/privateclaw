import type { AgentProfile, Capability } from "./capabilities.js";
import type { Personalization } from "./types.js";

export const LEGACY_TOOL_NAMES = [
  "get_system_time",
  "web_search",
  "deep_search",
  "execute_python_code",
  "create_new_skills",
  "exec_cli_command",
  "schedule_cli_command",
  "set_alarm",
] as const;

const ALL_RUNTIME_CAPABILITIES: Capability[] = [
  "agent:spawn",
  "memory:read",
  "memory:write",
  "network:web",
  "fs:read",
  "fs:write",
  "shell:exec",
  "code:exec",
  "external:feishu",
];

export function createOrchestratorProfile(personalization: Personalization): AgentProfile {
  return {
    name: "orchestrator",
    role: "orchestrator",
    model: personalization.models.fsm,
    systemPrompt:
      [
        "你是主控 Orchestrator Agent，只负责理解用户目标、拆分任务、调用 spawn_agent 委派给子 Agent，并汇总最终答案。",
        "你不能直接搜索、读写文件或运行命令；这些副作用必须通过 spawn_agent 委派给 researcher/coder/tester。",
        "常见委派：researcher=搜索和网页阅读；coder=读写授权代码文件；tester=运行白名单测试/构建命令。",
        "简单无需工具的问题可以直接回答。",
      ].join("\n"),
    permissions: {
      direct: {
        tools: ["spawn_agent", "get_system_time"],
        capabilities: ["agent:spawn", "memory:read"],
        resources: {
          deniedPaths: [".git/**", ".env", ".env.*", "personalization.yaml"],
          envKeys: [],
        },
      },
      delegate: {
        tools: [...LEGACY_TOOL_NAMES, "read_file", "write_file", "list_files", "read_url", "spawn_agent"],
        capabilities: [...ALL_RUNTIME_CAPABILITIES],
        resources: {
          readPaths: ["src/**", "README.md", "package.json", "tsconfig.json", "tool_config.yaml", "dynamic_config.yaml"],
          writePaths: ["src/**", "README.md", "requirement.md", "tool_config.yaml"],
          deniedPaths: [".git/**", ".env", ".env.*", "personalization.yaml"],
          commands: ["npm run typecheck", "npm run build", "npm test"],
          network: ["https://*"],
          envKeys: [],
        },
      },
    },
  };
}

export function createAssistantProfile(personalization: Personalization): AgentProfile {
  const profile: AgentProfile = {
    name: "assistant",
    role: "assistant",
    model: personalization.models.fsm,
    systemPrompt:
      "你是主 Agent。当前阶段保持兼容现有助手能力；所有工具调用必须经过 CapabilityBroker 权限裁决。后续会收窄为 orchestrator + subagent 委派模型。",
    permissions: {
      direct: {
        tools: [...LEGACY_TOOL_NAMES],
        capabilities: [...ALL_RUNTIME_CAPABILITIES],
        resources: {
          readPaths: ["*"],
          writePaths: ["*"],
          deniedPaths: [".git/**", ".env", ".env.*"],
          commands: ["*"],
          network: ["https://*", "http://*"],
          envKeys: [],
        },
      },
    },
  };
  const delegate = createOrchestratorProfile(personalization).permissions.delegate;
  if (delegate) {
    profile.permissions.delegate = delegate;
  }
  return profile;
}

export const AgentProfiles = {
  researcher: {
    name: "researcher",
    role: "researcher",
    model: "qwen-max",
    systemPrompt: "你是研究 Agent，只能搜索、阅读网页、摘录证据，并输出带来源的研究结论。",
    permissions: {
      direct: {
        tools: ["web_search", "deep_search", "read_url"],
        capabilities: ["network:web"],
        resources: {
          network: ["https://*", "http://*"],
        },
      },
    },
  },
  coder: {
    name: "coder",
    role: "coder",
    model: "qwen-max",
    systemPrompt: "你是代码 Agent，只能读写被授权路径内的文件，不能运行 shell 命令。",
    permissions: {
      direct: {
        tools: ["read_file", "write_file", "list_files"],
        capabilities: ["fs:read", "fs:write"],
        resources: {
          readPaths: ["src/**"],
          writePaths: ["src/**"],
          deniedPaths: [".git/**", ".env", ".env.*", "personalization.yaml"],
        },
      },
    },
  },
  tester: {
    name: "tester",
    role: "tester",
    model: "qwen-max",
    systemPrompt: "你是测试 Agent，只能运行白名单测试/构建命令。",
    permissions: {
      direct: {
        tools: ["exec_cli_command"],
        capabilities: ["shell:exec"],
        resources: {
          commands: ["npm run typecheck", "npm run build", "npm test"],
        },
      },
    },
  },
} satisfies Record<string, AgentProfile>;
