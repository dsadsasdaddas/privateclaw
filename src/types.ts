export interface RuntimeMessage {
  session_id: string;
  text: string;
  source: string;
  chat_type: string;
  chat_id: string;
  message_id: string;
  user_scope_id: string;
  conversation_id?: string;
  dedup_key?: string;
  enqueue_ts_ms?: number;
}

export interface RuntimeResult {
  session_id: string;
  conversation_id: string;
  user_scope_id: string;
  text: string;
}

export interface PersonalizationModels {
  chat: string;
  router: string;
  fsm: string;
  plan: string;
  summary: string;
}

export interface Personalization {
  api_key_env: string;
  base_url: string;
  models: PersonalizationModels;
  deepsearch_trigger_keyword?: string;
}

export interface LoopToolCall {
  id: string;
  name: string;
  arguments: string;
}

export interface LoopDecision {
  kind: "answer" | "tool_calls" | "need_approval";
  answer?: string;
  tool_calls?: LoopToolCall[];
  approval_request?: {
    reason: string;
    tool_calls: LoopToolCall[];
  };
}

export type ChatHistoryItem = Record<string, unknown>;
export type ToolConfig = Record<string, unknown>[];
export type ToolFunction = (args: Record<string, unknown>) => unknown | Promise<unknown>;
export type AvailableTools = Record<string, ToolFunction>;

export interface SearchResult {
  title: string;
  snippet: string;
  url: string;
}
