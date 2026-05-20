import { randomUUID } from "node:crypto";
import type { ChatHistoryItem, LoopDecision, LoopToolCall, RuntimeMessage, RuntimeResult } from "./types.js";
import type { MemoryContextManager } from "./context-memory.js";
import type { Personalization } from "./types.js";
import { createRootExecutionContext, type AgentProfile, type CapabilityBroker, type ExecutionContext } from "./capabilities.js";

function shortId(prefix: string, length = 10): string {
  return `${prefix}-${randomUUID().replaceAll("-", "").slice(0, length)}`;
}

function getString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function formatToolResult(result: unknown): string {
  if (typeof result === "string") {
    return result;
  }
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

export class AgentLoop {
  private readonly runTimeoutSeconds = 60;
  private readonly maxStallSteps = 8;
  private readonly maxSameToolFailures = 3;
  private readonly nonRetriableErrorSignatures = [
    "approval required",
    "allowlist miss",
    "permission denied",
    "权限缺失",
    "权限不足",
    "forbidden",
    "not authorized",
    "节点不在前台",
    "not in foreground",
  ];

  private readonly sessionHistories = new Map<string, ChatHistoryItem[]>();
  private readonly sessionConversations = new Map<string, string>();

  constructor(
    private readonly client: any,
    private readonly memoryManager: MemoryContextManager,
    private readonly broker: CapabilityBroker,
    private readonly rootProfile: AgentProfile,
    private readonly personalization: Personalization,
  ) {}

  private debug(stage: string, detail = ""): void {
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    const time = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    const suffix = detail ? ` | ${detail}` : "";
    console.log(`[DEBUG] ${time} ${stage}${suffix}`);
  }

  private newConversationId(): string {
    return shortId("conv", 10);
  }

  private resolveConversationId(sessionId: string, requestedConversationId = ""): string {
    const conversationId = requestedConversationId.trim();
    if (conversationId) {
      this.sessionConversations.set(sessionId, conversationId);
      return conversationId;
    }
    const existing = this.sessionConversations.get(sessionId);
    if (existing) {
      return existing;
    }
    const created = this.newConversationId();
    this.sessionConversations.set(sessionId, created);
    return created;
  }

  private resetConversation(sessionId: string): string {
    const newId = this.newConversationId();
    this.sessionConversations.set(sessionId, newId);
    this.sessionHistories.set(newId, []);
    return newId;
  }

  private getOrCreateHistory(conversationId: string): ChatHistoryItem[] {
    const existing = this.sessionHistories.get(conversationId);
    if (existing) {
      return existing;
    }
    const created: ChatHistoryItem[] = [];
    this.sessionHistories.set(conversationId, created);
    return created;
  }

  private buildToolErrorMessage(toolCallId: string, name: string, reason: string): ChatHistoryItem {
    return {
      role: "tool",
      content: `tool call not completed: ${reason}`,
      tool_call_id: toolCallId,
      name,
    };
  }

  private repairHistory(history: ChatHistoryItem[]): ChatHistoryItem[] {
    const repaired: ChatHistoryItem[] = [];
    let i = 0;

    while (i < history.length) {
      const item = history[i] ?? {};
      repaired.push(item);
      const toolCalls = Array.isArray(item.tool_calls) ? (item.tool_calls as any[]) : undefined;

      if (item.role === "assistant" && toolCalls && toolCalls.length > 0) {
        const requiredIds = toolCalls.map((tc) => getString(tc?.id)).filter(Boolean);
        let j = i + 1;
        const matchedIds = new Set<string>();
        const bufferedFollowing: ChatHistoryItem[] = [];

        while (j < history.length) {
          const next = history[j] ?? {};
          if (next.role === "tool") {
            bufferedFollowing.push(next);
            const toolCallId = getString(next.tool_call_id);
            if (requiredIds.includes(toolCallId)) {
              matchedIds.add(toolCallId);
            }
            j += 1;
            continue;
          }
          break;
        }

        repaired.push(...bufferedFollowing);
        const missingIds = requiredIds.filter((id) => !matchedIds.has(id));
        for (const tc of toolCalls) {
          const tcId = getString(tc?.id);
          if (missingIds.includes(tcId)) {
            const name = getString(tc?.function?.name, "unknown");
            repaired.push(this.buildToolErrorMessage(tcId, name, "missing tool response patched"));
          }
        }

        i = j;
        continue;
      }

      i += 1;
    }

    return repaired;
  }

  private async plan(ctx: ExecutionContext, history: ChatHistoryItem[]): Promise<LoopDecision> {
    history.splice(0, history.length, ...this.repairHistory(history));
    this.debug("plan_start");

    const response = await this.client.chat.completions.create({
      model: ctx.profile.model || this.personalization.models.fsm,
      messages: [
        {
          role: "system",
          content: ctx.profile.systemPrompt,
        },
        {
          role: "system",
          content:
            "你是 Planner。优先直接回答；需要工具时发起 tool_calls；当问题需要多轮检索和网页阅读时优先调用 deep_search；危险工具先请求审批。",
        },
        { role: "system", content: this.memoryManager.buildSystemContext(ctx.userScopeId) },
        ...history,
      ],
      tools: this.broker.listToolSchemas(ctx) as any,
      stream: false,
    });

    const message = response.choices?.[0]?.message ?? { role: "assistant", content: "" };
    const msgDict = JSON.parse(JSON.stringify(message)) as ChatHistoryItem;
    if (msgDict.content === null || msgDict.content === undefined) {
      msgDict.content = "";
    }
    history.push(msgDict);

    const rawToolCalls = Array.isArray((message as any).tool_calls) ? ((message as any).tool_calls as any[]) : [];
    if (rawToolCalls.length > 0) {
      const toolCalls: LoopToolCall[] = rawToolCalls.map((toolCall, idx) => ({
        id: getString(toolCall.id, `tool-${idx + 1}`),
        name: getString(toolCall.function?.name),
        arguments: getString(toolCall.function?.arguments, "{}"),
      }));

      if (this.needsApproval(toolCalls)) {
        return {
          kind: "need_approval",
          approval_request: { reason: "sensitive_tool", tool_calls: toolCalls },
        };
      }
      return { kind: "tool_calls", tool_calls: toolCalls };
    }

    this.debug("plan_end", "answer");
    return { kind: "answer", answer: String((message as any).content ?? "").trim() };
  }

  private needsApproval(toolCalls: LoopToolCall[]): boolean {
    const sensitiveKeywords = ["delete", "remove", "exec", "shell", "write", "drop"];
    return toolCalls.some((call) => sensitiveKeywords.some((word) => call.name.toLowerCase().includes(word)));
  }

  private requestApproval(_msg: RuntimeMessage, approvalRequest: LoopDecision["approval_request"]): string {
    const toolNames = approvalRequest?.tool_calls.map((call) => call.name).join(",") ?? "";
    return `审批结果：自动批准。tools=${toolNames}`;
  }

  private toolFailureSignature(toolName: string, args: string): string {
    return `${toolName}::${args}`;
  }

  private isToolFailure(text: string): boolean {
    const lower = text.toLowerCase();
    return ["error", "failed", "failure", "exception", "命令执行失败", "命令执行异常", "json decode error", "not found"].some((marker) =>
      lower.includes(marker),
    );
  }

  private matchNonRetriableSignature(text: string): string {
    const lower = text.toLowerCase();
    return this.nonRetriableErrorSignatures.find((signature) => lower.includes(signature)) ?? "";
  }

  private async execute(
    ctx: ExecutionContext,
    toolCalls: LoopToolCall[],
    failureCounts: Map<string, number>,
  ): Promise<{ toolResults: ChatHistoryItem[]; hardStopReason: string }> {
    this.debug("execute_start", `count=${toolCalls.length}`);
    const toolResults: ChatHistoryItem[] = [];
    let hardStopReason = "";

    for (const [idx, toolCall] of toolCalls.entries()) {
      const funcName = toolCall.name;
      const funcArgsStr = toolCall.arguments || "{}";
      const callId = toolCall.id || `tool-${idx + 1}`;
      const signature = this.toolFailureSignature(funcName, funcArgsStr);

      let result: unknown;
      try {
        const parsedArgs = JSON.parse(funcArgsStr) as unknown;
        const jsonArgs = typeof parsedArgs === "object" && parsedArgs !== null ? (parsedArgs as Record<string, unknown>) : {};
        result = await this.broker.call(ctx, funcName, jsonArgs);
      } catch (error) {
        result = error instanceof SyntaxError ? `Tool arguments JSON decode error: ${error.message}` : `Error executing tool '${funcName}': ${error instanceof Error ? error.message : String(error)}`;
      }

      const resultText = formatToolResult(result);
      const nonRetrySignature = this.matchNonRetriableSignature(resultText);
      if (nonRetrySignature) {
        hardStopReason = `检测到不可重试错误签名: \`${nonRetrySignature}\`。工具 \`${funcName}\` 返回：${resultText}`;
      }

      if (this.isToolFailure(resultText)) {
        const count = (failureCounts.get(signature) ?? 0) + 1;
        failureCounts.set(signature, count);
        if (count >= this.maxSameToolFailures && !hardStopReason) {
          hardStopReason = `同一工具与参数连续失败已达 ${this.maxSameToolFailures} 次，停止重试。工具=\`${funcName}\` 参数=\`${funcArgsStr}\` 最近报错：${resultText}`;
        }
      } else {
        failureCounts.set(signature, 0);
      }

      toolResults.push({
        role: "tool",
        content: resultText,
        tool_call_id: callId,
        name: funcName,
      });

      if (hardStopReason) {
        break;
      }
    }

    this.debug("execute_end");
    return { toolResults, hardStopReason };
  }

  async runSubagentTask(task: string, ctx: ExecutionContext): Promise<string> {
    if (ctx.depth > 3) {
      return `subagent refused: max depth exceeded. depth=${ctx.depth}`;
    }
    const result = await this.runWithContext({
      userInput: task,
      sessionId: `${ctx.conversationId}:${ctx.agentId}`,
      ctx,
      queueWaitMs: 0,
      dedupKey: "",
      persistMemory: false,
      allowResetCommand: false,
    });
    return result.text;
  }

  async run(msg: RuntimeMessage): Promise<RuntimeResult> {
    const runId = shortId("run", 8);
    const sessionId = msg.session_id;
    const userInput = (msg.text ?? "").trim();
    const userScopeId = (msg.user_scope_id || sessionId).trim() || sessionId;
    const conversationId = this.resolveConversationId(sessionId, msg.conversation_id ?? "");
    const ctx = createRootExecutionContext({
      profile: this.rootProfile,
      conversationId,
      userScopeId,
      taskId: runId,
    });
    const queueWaitMs = msg.enqueue_ts_ms ? Math.max(0, Date.now() - msg.enqueue_ts_ms) : 0;
    return this.runWithContext({
      userInput,
      sessionId,
      ctx,
      queueWaitMs,
      dedupKey: msg.dedup_key ?? "",
      persistMemory: true,
      allowResetCommand: true,
    });
  }

  private async runWithContext(args: {
    userInput: string;
    sessionId: string;
    ctx: ExecutionContext;
    queueWaitMs: number;
    dedupKey: string;
    persistMemory: boolean;
    allowResetCommand: boolean;
  }): Promise<RuntimeResult> {
    const runId = args.ctx.taskId;
    const sessionId = args.sessionId;
    const userInput = args.userInput.trim();
    const userScopeId = args.ctx.userScopeId;
    let conversationId = args.ctx.conversationId;
    const queueWaitMs = args.queueWaitMs;
    let llmMsTotal = 0;
    let toolMsTotal = 0;
    let memoryMsTotal = 0;

    if (!userInput) {
      this.debug(
        "run_metrics",
        `run_id=${runId} agent_id=${args.ctx.agentId} role=${args.ctx.profile.role} session_id=${sessionId} conversation_id=${conversationId} queue_wait_ms=${queueWaitMs} llm_ms=${llmMsTotal} tool_ms=${toolMsTotal} memory_ms=${memoryMsTotal} dedup_key=${args.dedupKey}`,
      );
      return { session_id: sessionId, conversation_id: conversationId, user_scope_id: userScopeId, text: "Empty input." };
    }

    if (args.allowResetCommand && userInput === "/reset") {
      const newConversationId = this.resetConversation(sessionId);
      this.debug(
        "run_metrics",
        `run_id=${runId} agent_id=${args.ctx.agentId} role=${args.ctx.profile.role} session_id=${sessionId} conversation_id=${newConversationId} queue_wait_ms=${queueWaitMs} llm_ms=${llmMsTotal} tool_ms=${toolMsTotal} memory_ms=${memoryMsTotal} dedup_key=${args.dedupKey}`,
      );
      return {
        session_id: sessionId,
        conversation_id: newConversationId,
        user_scope_id: userScopeId,
        text: `会话已重置，新短期会话ID: ${newConversationId}`,
      };
    }

    const historyKey = args.persistMemory ? conversationId : `${conversationId}:${args.ctx.taskId}:${args.ctx.agentId}`;
    const history = this.getOrCreateHistory(historyKey);
    history.push({ role: "user", content: userInput });

    let state: "PLANNING" | "EXECUTING" | "OBSERVING" = "PLANNING";
    let pendingToolCalls: LoopToolCall[] = [];
    let finalAnswer = "";
    const loopStart = performance.now();
    const failureCounts = new Map<string, number>();
    let lastSnapshot = "";
    let stallSteps = 0;

    for (let step = 0; step < 64; step += 1) {
      if ((performance.now() - loopStart) / 1000 > this.runTimeoutSeconds) {
        finalAnswer = "本轮处理超过 60 秒已强制结束，请你根据当前报错继续排障，我已把控制权交还给你。";
        break;
      }

      const snapshot = `${state}|${history.length}|${pendingToolCalls.length}|${finalAnswer}`;
      if (snapshot === lastSnapshot) {
        stallSteps += 1;
      } else {
        stallSteps = 0;
        lastSnapshot = snapshot;
      }
      if (stallSteps >= this.maxStallSteps) {
        finalAnswer = "连续 8 步无状态变化，已终止本轮处理。请提供更具体输入或调整权限/参数后重试。";
        break;
      }

      if (state === "PLANNING") {
        const llmStart = performance.now();
        const decision = await this.plan(args.ctx, history);
        llmMsTotal += Math.floor(performance.now() - llmStart);
        if (decision.kind === "answer") {
          finalAnswer = decision.answer ?? "";
          break;
        }
        if (decision.kind === "tool_calls") {
          pendingToolCalls = decision.tool_calls ?? [];
          state = "EXECUTING";
          continue;
        }
        if (decision.kind === "need_approval") {
          const approvalResult = this.requestApproval(
            {
              session_id: sessionId,
              text: userInput,
              source: args.persistMemory ? "runtime" : "subagent",
              chat_type: "internal",
              chat_id: sessionId,
              message_id: "",
              user_scope_id: userScopeId,
              conversation_id: conversationId,
            },
            decision.approval_request,
          );
          this.debug("approval", approvalResult);
          pendingToolCalls = decision.approval_request?.tool_calls ?? [];
          state = "EXECUTING";
          continue;
        }
      }

      if (state === "EXECUTING") {
        const toolStart = performance.now();
        const { toolResults, hardStopReason } = await this.execute(args.ctx, pendingToolCalls, failureCounts);
        toolMsTotal += Math.floor(performance.now() - toolStart);
        history.push(...toolResults);
        if (hardStopReason) {
          finalAnswer = `工具执行已停止，原因如下：\n${hardStopReason}\n\n这类错误通常不应继续自动重试，请你确认权限、allowlist、审批状态或前台节点状态后再继续。`;
          break;
        }
        state = "OBSERVING";
        continue;
      }

      if (state === "OBSERVING") {
        state = "PLANNING";
      }
    }

    if (!finalAnswer) {
      finalAnswer = "处理超出最大轮次，请简化问题后重试。";
    }

    if (finalAnswer) {
      history.push({ role: "assistant", content: finalAnswer });
    }
    history.splice(0, history.length, ...this.repairHistory(history));

    if (args.persistMemory) {
      const memoryStart = performance.now();
      await this.memoryManager.updateMemory(userInput, finalAnswer, userScopeId);
      await this.memoryManager.maybeUpdateSoul(userScopeId);
      const compactedHistory = await this.memoryManager.compactHistoryIfNeeded(history, 256_000, userScopeId);
      memoryMsTotal += Math.floor(performance.now() - memoryStart);

      if (compactedHistory !== history) {
        const newConversationId = this.newConversationId();
        this.sessionConversations.set(sessionId, newConversationId);
        this.sessionHistories.set(newConversationId, compactedHistory);
        this.sessionHistories.delete(conversationId);
        conversationId = newConversationId;
      } else {
        this.sessionHistories.set(conversationId, compactedHistory);
      }
    } else {
      this.sessionHistories.set(historyKey, history);
    }

    this.debug(
      "run_metrics",
      `run_id=${runId} agent_id=${args.ctx.agentId} role=${args.ctx.profile.role} session_id=${sessionId} conversation_id=${conversationId} queue_wait_ms=${queueWaitMs} llm_ms=${llmMsTotal} tool_ms=${toolMsTotal} memory_ms=${memoryMsTotal} dedup_key=${args.dedupKey}`,
    );

    return {
      session_id: sessionId,
      conversation_id: conversationId,
      user_scope_id: userScopeId,
      text: finalAnswer,
    };
  }
}
