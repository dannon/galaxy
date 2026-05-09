import type { ActionSuggestion, AgentResponse } from "@/composables/agentActions";

export interface ChatMessage {
    id: string;
    role: "user" | "assistant";
    content: string;
    timestamp: Date;
    agentType?: string;
    confidence?: string;
    feedback?: "up" | "down" | null;
    agentResponse?: AgentResponse;
    suggestions?: ActionSuggestion[];
    isSystemMessage?: boolean;
    // Streaming state. Only meaningful while the placeholder is being filled
    // by chat_event SSE frames; cleared on done/error so the meta footer
    // (feedback, model tag, etc.) renders the same as the synchronous path.
    inProgress?: boolean;
    activeTool?: string | null;
}

export interface ChatHistoryItem {
    id: string;
    query: string;
    response: string;
    agent_type: string;
    agent_response?: AgentResponse;
    timestamp: string;
    feedback?: number | null;
}
