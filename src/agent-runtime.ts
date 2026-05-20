import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import type { RuntimeMessage, RuntimeResult } from "./types.js";
import type { AgentLoop } from "./agent-loop.js";

function isRuntimeMessage(value: unknown): value is RuntimeMessage {
  return typeof value === "object" && value !== null && "session_id" in value && "text" in value;
}

export class AgentRuntime {
  constructor(private readonly agentLoop: AgentLoop) {}

  handleMessage(msg: RuntimeMessage): Promise<RuntimeResult> {
    return this.agentLoop.run(msg);
  }

  async handleInput(payload: RuntimeMessage | Record<string, unknown> | string): Promise<RuntimeResult> {
    if (isRuntimeMessage(payload)) {
      return this.handleMessage(payload);
    }

    if (typeof payload === "object" && payload !== null) {
      const sessionId = String(payload.session_id ?? "").trim() || "local-cli";
      const msg: RuntimeMessage = {
        session_id: sessionId,
        text: String(payload.text ?? ""),
        source: String(payload.source ?? "cli"),
        chat_type: String(payload.chat_type ?? "cli"),
        chat_id: String(payload.chat_id ?? "local"),
        message_id: String(payload.message_id ?? ""),
        user_scope_id: String(payload.user_scope_id ?? payload.session_id ?? "local-cli"),
        conversation_id: String(payload.conversation_id ?? ""),
      };
      return this.handleMessage(msg);
    }

    return this.handleMessage({
      session_id: "local-cli",
      text: String(payload),
      source: "cli",
      chat_type: "cli",
      chat_id: "local",
      message_id: "",
      user_scope_id: "local-cli",
      conversation_id: "",
    });
  }

  async processChannelMessage(channel: { sendReply(data: unknown, text: string): void | Promise<void> }, data: unknown, msg: RuntimeMessage): Promise<RuntimeResult> {
    const result = await this.handleMessage(msg);
    await channel.sendReply(data, result.text);
    return result;
  }

  async run(): Promise<void> {
    const rl = createInterface({ input, output });
    try {
      while (true) {
        const userInput = await rl.question("User:");
        if (userInput === "quit") {
          break;
        }
        const result = await this.handleInput(userInput);
        console.log(`output:${result.text}`);
      }
    } finally {
      rl.close();
    }
  }
}
