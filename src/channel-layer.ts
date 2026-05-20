import type { RuntimeMessage } from "./types.js";

function asString(value: unknown, fallback = ""): string {
  if (value === undefined || value === null) {
    return fallback;
  }
  return String(value);
}

function safeJsonObject(raw: unknown): Record<string, unknown> {
  if (typeof raw !== "string") {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function safeGetSenderOpenId(data: any): string {
  return asString(data?.event?.sender?.sender_id?.open_id, "unknown") || "unknown";
}

export function normalizeFeishuEvent(data: any, conversationId = ""): RuntimeMessage {
  const message = data?.event?.message ?? {};
  const content = safeJsonObject(message.content);
  const text = message.message_type === "text" ? asString(content.text).trim() : "";

  const senderOpenId = safeGetSenderOpenId(data);
  const userScopeId = `feishu:user:${senderOpenId}`;
  const chatType = asString(message.chat_type, "");
  const chatId = asString(message.chat_id, "");

  const sessionBase =
    chatType === "p2p" ? `feishu:p2p:${chatId}` : `feishu:group:${chatId}:${senderOpenId}`;
  const conv = conversationId.trim();

  return {
    session_id: conv ? `${sessionBase}:${conv}` : sessionBase,
    text,
    source: "feishu",
    chat_type: chatType,
    chat_id: chatId,
    message_id: asString(message.message_id, ""),
    user_scope_id: userScopeId,
    conversation_id: conv,
  };
}

export function runtimePayloadFromFeishu(data: any): Record<string, string> {
  const normalized = normalizeFeishuEvent(data);
  return {
    session_id: normalized.session_id,
    text: normalized.text,
    source: normalized.source,
    chat_type: normalized.chat_type,
    chat_id: normalized.chat_id,
    message_id: normalized.message_id,
    user_scope_id: normalized.user_scope_id,
    conversation_id: normalized.conversation_id ?? "",
  };
}
