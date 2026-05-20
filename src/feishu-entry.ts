import type { AgentRuntime } from "./agent-runtime.js";
import type { RuntimeMessage, RuntimeResult } from "./types.js";
import { normalizeFeishuEvent } from "./channel-layer.js";

interface QueueTask {
  type: "reply_text" | "handle_runtime";
  data: any;
  content: string | RuntimeMessage;
}

export class FeishuEntry {
  private readonly appId = (process.env.LARK_APP_ID ?? "").trim();
  private readonly appSecret = (process.env.LARK_APP_SECRET ?? "").trim();
  private readonly dedupTtlSeconds = Number(process.env.FEISHU_DEDUP_TTL_SECONDS ?? "7200");
  private readonly recentEventKeys = new Map<string, number>();
  private readonly activeConversations = new Map<string, string>();
  private queueChain: Promise<void> = Promise.resolve();
  private client: any;
  private wsClient: any;
  private eventDispatcher: any;
  private larkModule: any;

  constructor(private readonly runtime: AgentRuntime) {
    if (!this.appId || !this.appSecret) {
      throw new Error("Missing LARK_APP_ID/LARK_APP_SECRET for feishu message entry.");
    }
  }

  private newConversationId(): string {
    return `conv-${Math.random().toString(16).slice(2, 12)}`;
  }

  private conversationKey(userScopeId: string, chatId: string): string {
    return `${userScopeId}:${chatId}`;
  }

  private getOrCreateConversationId(userScopeId: string, chatId: string): string {
    const key = this.conversationKey(userScopeId, chatId);
    const existing = this.activeConversations.get(key);
    if (existing) {
      return existing;
    }
    const created = this.newConversationId();
    this.activeConversations.set(key, created);
    return created;
  }

  private setConversationId(userScopeId: string, chatId: string, conversationId: string): void {
    if (!conversationId) {
      return;
    }
    this.activeConversations.set(this.conversationKey(userScopeId, chatId), conversationId);
  }

  private async init(): Promise<void> {
    if (this.client && this.wsClient && this.eventDispatcher) {
      return;
    }

    const imported = await import("@larksuiteoapi/node-sdk");
    this.larkModule = (imported as any).default ?? imported;

    this.client = new this.larkModule.Client({ appId: this.appId, appSecret: this.appSecret });
    this.wsClient = new this.larkModule.WSClient({ appId: this.appId, appSecret: this.appSecret });

    if (this.larkModule.EventDispatcher) {
      this.eventDispatcher = new this.larkModule.EventDispatcher({}).register({
        "im.message.receive_v1": async (data: any) => this.onMessage(data),
      });
      return;
    }

    if (this.larkModule.EventDispatcherHandler?.builder) {
      this.eventDispatcher = this.larkModule.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(async (data: any) => this.onMessage(data))
        .build();
      return;
    }

    throw new Error("Unsupported @larksuiteoapi/node-sdk event dispatcher API.");
  }

  async sendReply(data: any, text: string): Promise<void> {
    await this.init();
    const content = JSON.stringify({ text });
    const message = data?.event?.message ?? {};

    let resp: any;
    if (message.chat_type === "p2p") {
      resp = await this.client.im.message.create({
        params: { receive_id_type: "chat_id" },
        data: {
          receive_id: message.chat_id,
          msg_type: "text",
          content,
        },
      });
    } else {
      resp = await this.client.im.message.reply({
        path: { message_id: message.message_id },
        data: {
          msg_type: "text",
          content,
        },
      });
    }

    if (resp && typeof resp.code === "number" && resp.code !== 0) {
      throw new Error(`send feishu message failed: ${resp.code}, ${resp.msg ?? ""}`);
    }
  }

  private onMessage(data: any): void {
    const baseMsg = normalizeFeishuEvent(data);
    const conversationId = this.getOrCreateConversationId(baseMsg.user_scope_id, baseMsg.chat_id);
    const msg = normalizeFeishuEvent(data, conversationId);
    const dedupKey = this.buildDedupKey(data, msg.message_id, msg.session_id, msg.text);

    if (this.isDuplicateEvent(dedupKey)) {
      console.log(`[DEBUG] skip duplicated feishu event: ${dedupKey}`);
      return;
    }

    msg.dedup_key = dedupKey;
    msg.enqueue_ts_ms = Date.now();

    if (!msg.text) {
      this.enqueue({ type: "reply_text", data, content: "parse message failed, please send text message" });
      return;
    }

    this.enqueue({ type: "handle_runtime", data, content: msg });
  }

  private enqueue(task: QueueTask): void {
    this.queueChain = this.queueChain
      .then(() => this.handleTask(task))
      .catch((error) => {
        console.error(`[ERROR] worker handle message failed: ${error instanceof Error ? error.message : String(error)}`);
      });
  }

  private async handleTask(task: QueueTask): Promise<void> {
    try {
      if (task.type === "reply_text") {
        await this.sendReply(task.data, String(task.content));
        return;
      }

      const msg = task.content as RuntimeMessage;
      const queueWaitMs = msg.enqueue_ts_ms ? Math.max(0, Date.now() - msg.enqueue_ts_ms) : 0;
      console.log(
        `[DEBUG] queue_metrics session_id=${msg.session_id} conversation_id=${msg.conversation_id ?? ""} queue_wait_ms=${queueWaitMs} dedup_key=${msg.dedup_key ?? ""}`,
      );
      const result: RuntimeResult = await this.runtime.processChannelMessage(this, task.data, msg);
      this.setConversationId(msg.user_scope_id, msg.chat_id, result.conversation_id);
    } catch (error) {
      console.error(`[ERROR] worker handle message failed: ${error instanceof Error ? error.message : String(error)}`);
      try {
        await this.sendReply(task.data, "处理消息时发生错误，请稍后重试。");
      } catch (sendError) {
        console.error(`[ERROR] send error message failed: ${sendError instanceof Error ? sendError.message : String(sendError)}`);
      }
    }
  }

  private buildDedupKey(data: any, messageId: string, sessionId: string, text: string): string {
    const eventId = String(data?.header?.event_id ?? "").trim();
    if (eventId) {
      return `event_id:${eventId}`;
    }
    if (messageId) {
      return `message_id:${messageId}`;
    }
    return `session_text:${sessionId}:${text}`;
  }

  private isDuplicateEvent(dedupKey: string): boolean {
    const now = Date.now() / 1000;
    for (const [key, expiresAt] of this.recentEventKeys.entries()) {
      if (expiresAt <= now) {
        this.recentEventKeys.delete(key);
      }
    }

    if (this.recentEventKeys.has(dedupKey)) {
      return true;
    }
    this.recentEventKeys.set(dedupKey, now + this.dedupTtlSeconds);
    return false;
  }

  async run(): Promise<void> {
    await this.init();
    if (typeof this.wsClient.start !== "function") {
      throw new Error("Feishu WS client does not expose start().");
    }

    const startResult = this.wsClient.start({ eventDispatcher: this.eventDispatcher });
    if (startResult && typeof startResult.then === "function") {
      await startResult;
    }
  }
}
