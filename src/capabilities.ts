import { randomUUID } from "node:crypto";

export type Capability =
  | "agent:spawn"
  | "memory:read"
  | "memory:write"
  | "network:web"
  | "fs:read"
  | "fs:write"
  | "shell:exec"
  | "code:exec"
  | "external:feishu";

export interface ResourceScope {
  readPaths?: string[];
  writePaths?: string[];
  deniedPaths?: string[];
  commands?: string[];
  network?: string[];
  envKeys?: string[];
}

export interface GrantSet {
  tools: string[];
  capabilities: Capability[];
  resources?: ResourceScope;
}

export interface AgentProfile {
  name: string;
  role: string;
  model: string;
  systemPrompt: string;
  permissions: {
    direct: GrantSet;
    delegate?: GrantSet;
  };
}

export interface ExecutionContext {
  agentId: string;
  parentAgentId?: string;
  profile: AgentProfile;
  conversationId: string;
  userScopeId: string;
  taskId: string;
  effectiveGrants: GrantSet;
  depth: number;
}

export interface PermissionDecision {
  allowed: boolean;
  reason: string;
}

export interface ToolAuditEvent {
  ts: number;
  agentId: string;
  parentAgentId?: string;
  role: string;
  profileName: string;
  conversationId: string;
  taskId: string;
  toolName: string;
  decision: PermissionDecision;
  argsPreview: string;
}

export interface AuditLogger {
  log(event: ToolAuditEvent): void;
}

export interface ToolSpec {
  name: string;
  schema: Record<string, unknown>;
  requiredCapabilities: Capability[];
  resourceKind?: "command" | "network" | "readPath" | "writePath" | "none";
  run: (args: Record<string, unknown>, ctx: ExecutionContext) => Promise<unknown> | unknown;
}

export class InMemoryAuditLogger implements AuditLogger {
  readonly events: ToolAuditEvent[] = [];

  log(event: ToolAuditEvent): void {
    this.events.push(event);
    if (process.env.AIGC_CLI_AUDIT_STDOUT === "1" || process.env.PRIVATECLAW_AUDIT_STDOUT === "1") {
      console.log(`[AUDIT] ${JSON.stringify(event)}`);
    }
  }
}

export function newAgentId(prefix = "agent"): string {
  return `${prefix}-${randomUUID().replaceAll("-", "").slice(0, 10)}`;
}

export function newTaskId(prefix = "task"): string {
  return `${prefix}-${randomUUID().replaceAll("-", "").slice(0, 10)}`;
}

export function createRootExecutionContext(args: {
  profile: AgentProfile;
  conversationId: string;
  userScopeId: string;
  taskId?: string;
  agentId?: string;
}): ExecutionContext {
  return {
    agentId: args.agentId ?? newAgentId("root"),
    profile: args.profile,
    conversationId: args.conversationId,
    userScopeId: args.userScopeId,
    taskId: args.taskId ?? newTaskId(),
    effectiveGrants: cloneGrantSet(args.profile.permissions.direct),
    depth: 0,
  };
}

export function cloneGrantSet(grants: GrantSet): GrantSet {
  const cloned: GrantSet = {
    tools: [...grants.tools],
    capabilities: [...grants.capabilities],
  };
  if (grants.resources) {
    cloned.resources = cloneResourceScope(grants.resources);
  }
  return cloned;
}

function cloneResourceScope(scope: ResourceScope): ResourceScope {
  const cloned: ResourceScope = {};
  if (scope.readPaths) cloned.readPaths = [...scope.readPaths];
  if (scope.writePaths) cloned.writePaths = [...scope.writePaths];
  if (scope.deniedPaths) cloned.deniedPaths = [...scope.deniedPaths];
  if (scope.commands) cloned.commands = [...scope.commands];
  if (scope.network) cloned.network = [...scope.network];
  if (scope.envKeys) cloned.envKeys = [...scope.envKeys];
  return cloned;
}

function uniqueIntersection<T>(a: T[] = [], b: T[] = []): T[] {
  const bSet = new Set(b);
  return [...new Set(a.filter((item) => bSet.has(item)))];
}

function intersectStringScopes(a?: string[], b?: string[]): string[] | undefined {
  if (!a && !b) {
    return undefined;
  }
  if (!a) {
    return [...(b ?? [])];
  }
  if (!b) {
    return [...a];
  }
  if (a.includes("*")) {
    return [...b];
  }
  if (b.includes("*")) {
    return [...a];
  }
  return uniqueIntersection(a, b);
}

function buildResourceScope(parts: {
  readPaths?: string[] | undefined;
  writePaths?: string[] | undefined;
  deniedPaths?: string[] | undefined;
  commands?: string[] | undefined;
  network?: string[] | undefined;
  envKeys?: string[] | undefined;
}): ResourceScope | undefined {
  const scope: ResourceScope = {};
  if (parts.readPaths !== undefined) scope.readPaths = parts.readPaths;
  if (parts.writePaths !== undefined) scope.writePaths = parts.writePaths;
  if (parts.deniedPaths !== undefined) scope.deniedPaths = parts.deniedPaths;
  if (parts.commands !== undefined) scope.commands = parts.commands;
  if (parts.network !== undefined) scope.network = parts.network;
  if (parts.envKeys !== undefined) scope.envKeys = parts.envKeys;
  return Object.keys(scope).length > 0 ? scope : undefined;
}

export function intersectGrantSets(a: GrantSet, b: GrantSet, requested?: Partial<ResourceScope>): GrantSet {
  const baseResources = buildResourceScope({
    readPaths: intersectStringScopes(a.resources?.readPaths, b.resources?.readPaths),
    writePaths: intersectStringScopes(a.resources?.writePaths, b.resources?.writePaths),
    deniedPaths: [...new Set([...(a.resources?.deniedPaths ?? []), ...(b.resources?.deniedPaths ?? [])])],
    commands: intersectStringScopes(a.resources?.commands, b.resources?.commands),
    network: intersectStringScopes(a.resources?.network, b.resources?.network),
    envKeys: intersectStringScopes(a.resources?.envKeys, b.resources?.envKeys),
  });
  const base: GrantSet = {
    tools: uniqueIntersection(a.tools, b.tools),
    capabilities: uniqueIntersection(a.capabilities, b.capabilities),
  };
  if (baseResources) {
    base.resources = baseResources;
  }

  if (!requested) {
    return base;
  }

  const requestedResources = buildResourceScope({
    readPaths: intersectStringScopes(base.resources?.readPaths, requested.readPaths),
    writePaths: intersectStringScopes(base.resources?.writePaths, requested.writePaths),
    deniedPaths: [...new Set([...(base.resources?.deniedPaths ?? []), ...(requested.deniedPaths ?? [])])],
    commands: intersectStringScopes(base.resources?.commands, requested.commands),
    network: intersectStringScopes(base.resources?.network, requested.network),
    envKeys: intersectStringScopes(base.resources?.envKeys, requested.envKeys),
  });
  const narrowed: GrantSet = {
    ...base,
  };
  if (requestedResources) {
    narrowed.resources = requestedResources;
  }
  return narrowed;
}

export function deriveChildContext(args: {
  parent: ExecutionContext;
  childProfile: AgentProfile;
  requestedScope?: Partial<ResourceScope>;
  taskId?: string;
  agentId?: string;
}): ExecutionContext {
  const parentDelegate = args.parent.profile.permissions.delegate;
  if (!args.parent.effectiveGrants.capabilities.includes("agent:spawn") || !parentDelegate) {
    throw new Error(`${args.parent.profile.role} cannot delegate subagents`);
  }

  return {
    agentId: args.agentId ?? newAgentId(args.childProfile.role),
    parentAgentId: args.parent.agentId,
    profile: args.childProfile,
    conversationId: args.parent.conversationId,
    userScopeId: args.parent.userScopeId,
    taskId: args.taskId ?? newTaskId(),
    effectiveGrants: intersectGrantSets(args.childProfile.permissions.direct, parentDelegate, args.requestedScope),
    depth: args.parent.depth + 1,
  };
}

function previewArgs(args: Record<string, unknown>): string {
  try {
    return JSON.stringify(args).slice(0, 500);
  } catch {
    return String(args).slice(0, 500);
  }
}

function matchSimplePattern(value: string, pattern: string): boolean {
  if (pattern === "*") {
    return true;
  }
  if (pattern.endsWith("/**")) {
    return value === pattern.slice(0, -3) || value.startsWith(pattern.slice(0, -2));
  }
  if (pattern.includes("*")) {
    const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replaceAll("*", ".*");
    return new RegExp(`^${escaped}$`).test(value);
  }
  return value === pattern;
}

function isAllowedByPatterns(value: string, allowPatterns?: string[], denyPatterns?: string[]): PermissionDecision {
  if (denyPatterns?.some((pattern) => matchSimplePattern(value, pattern))) {
    return { allowed: false, reason: `${value} is denied by scope` };
  }
  if (!allowPatterns || allowPatterns.length === 0) {
    return { allowed: false, reason: `no scope grants ${value}` };
  }
  if (allowPatterns.some((pattern) => matchSimplePattern(value, pattern))) {
    return { allowed: true, reason: "ok" };
  }
  return { allowed: false, reason: `${value} is outside allowed scope` };
}

export class CapabilityBroker {
  constructor(
    private readonly tools: Record<string, ToolSpec>,
    private readonly audit: AuditLogger,
  ) {}

  listToolSchemas(ctx: ExecutionContext): Record<string, unknown>[] {
    return Object.values(this.tools)
      .filter((tool) => this.canSeeTool(ctx, tool))
      .map((tool) => tool.schema);
  }

  async call(ctx: ExecutionContext, toolName: string, args: Record<string, unknown>): Promise<unknown> {
    const decision = this.check(ctx, toolName, args);
    const event: ToolAuditEvent = {
      ts: Date.now(),
      agentId: ctx.agentId,
      role: ctx.profile.role,
      profileName: ctx.profile.name,
      conversationId: ctx.conversationId,
      taskId: ctx.taskId,
      toolName,
      decision,
      argsPreview: previewArgs(args),
    };
    if (ctx.parentAgentId) {
      event.parentAgentId = ctx.parentAgentId;
    }
    this.audit.log(event);

    if (!decision.allowed) {
      return `denied: ${decision.reason}`;
    }

    return this.tools[toolName]?.run(args, ctx);
  }

  check(ctx: ExecutionContext, toolName: string, args: Record<string, unknown>): PermissionDecision {
    const tool = this.tools[toolName];
    if (!tool) {
      return { allowed: false, reason: `unknown tool ${toolName}` };
    }

    if (!ctx.effectiveGrants.tools.includes(toolName)) {
      return { allowed: false, reason: `${ctx.profile.role} cannot call ${toolName}` };
    }

    for (const capability of tool.requiredCapabilities) {
      if (!ctx.effectiveGrants.capabilities.includes(capability)) {
        return { allowed: false, reason: `${ctx.profile.role} lacks capability ${capability}` };
      }
    }

    return this.checkResources(ctx, tool, args);
  }

  private canSeeTool(ctx: ExecutionContext, tool: ToolSpec): boolean {
    return ctx.effectiveGrants.tools.includes(tool.name) && tool.requiredCapabilities.every((capability) => ctx.effectiveGrants.capabilities.includes(capability));
  }

  private checkResources(ctx: ExecutionContext, tool: ToolSpec, args: Record<string, unknown>): PermissionDecision {
    const resources = ctx.effectiveGrants.resources;
    switch (tool.resourceKind) {
      case "command": {
        const command = String(args.command ?? "").trim();
        return isAllowedByPatterns(command, resources?.commands);
      }
      case "network": {
        const target = String(args.url ?? args.query ?? "https://*").trim() || "https://*";
        return isAllowedByPatterns(target.startsWith("http") ? target : "https://*", resources?.network);
      }
      case "readPath": {
        const filePath = String(args.path ?? args.file_path ?? "").trim();
        return isAllowedByPatterns(filePath, resources?.readPaths, resources?.deniedPaths);
      }
      case "writePath": {
        const filePath = String(args.path ?? args.file_path ?? "").trim();
        return isAllowedByPatterns(filePath, resources?.writePaths, resources?.deniedPaths);
      }
      case "none":
      case undefined:
        return { allowed: true, reason: "ok" };
    }
    return { allowed: false, reason: `unsupported resource kind ${String(tool.resourceKind)}` };
  }
}
