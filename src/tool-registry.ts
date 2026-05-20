import type { Capability, ExecutionContext, ToolSpec } from "./capabilities.js";
import type { AvailableTools, ToolConfig } from "./types.js";

const TOOL_CAPABILITIES: Record<string, Capability[]> = {
  get_system_time: [],
  set_alarm: [],
  web_search: ["network:web"],
  deep_search: ["network:web"],
  execute_python_code: ["code:exec"],
  create_new_skills: ["fs:write"],
  exec_cli_command: ["shell:exec"],
  schedule_cli_command: ["shell:exec"],
  read_url: ["network:web"],
  read_file: ["fs:read"],
  write_file: ["fs:write"],
  list_files: ["fs:read"],
};

const TOOL_RESOURCE_KIND: Record<string, ToolSpec["resourceKind"]> = {
  web_search: "network",
  deep_search: "network",
  exec_cli_command: "command",
  schedule_cli_command: "command",
  read_url: "network",
  read_file: "readPath",
  write_file: "writePath",
  list_files: "readPath",
};

function getSchemaName(schema: Record<string, unknown>): string {
  const fn = schema.function;
  if (typeof fn === "object" && fn !== null && "name" in fn) {
    return String((fn as Record<string, unknown>).name ?? "");
  }
  return "";
}

function defaultSchema(name: string): Record<string, unknown> {
  return {
    type: "function",
    function: {
      name,
      description: `Tool ${name}`,
      parameters: {
        type: "object",
        properties: {},
        required: [],
      },
    },
  };
}

export function buildToolRegistry(toolConfig: ToolConfig, availableTools: AvailableTools): Record<string, ToolSpec> {
  const schemasByName = new Map<string, Record<string, unknown>>();
  for (const schema of toolConfig) {
    const name = getSchemaName(schema);
    if (name) {
      schemasByName.set(name, schema);
    }
  }

  const registry: Record<string, ToolSpec> = {};
  for (const [name, handler] of Object.entries(availableTools)) {
    registry[name] = {
      name,
      schema: schemasByName.get(name) ?? defaultSchema(name),
      requiredCapabilities: TOOL_CAPABILITIES[name] ?? [],
      resourceKind: TOOL_RESOURCE_KIND[name] ?? "none",
      run: async (args: Record<string, unknown>, _ctx: ExecutionContext) => handler(args),
    };
  }

  return registry;
}
