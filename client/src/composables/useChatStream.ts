import { onScopeDispose } from "vue";

import { useSSE } from "@/composables/useNotificationSSE";

export type ChatStreamKind = "delta" | "tool_call_start" | "tool_call_end" | "handoff" | "done" | "error";

export interface ChatStreamPayload {
    kind: ChatStreamKind;
    run_id: string;
    exchange_id: string | null;
    seq: number;
    [key: string]: unknown;
}

export interface ChatStreamHandlers {
    onDelta?: (text: string, payload: ChatStreamPayload) => void;
    onToolCallStart?: (
        info: { tool_name: string; tool_call_id: string; args: Record<string, unknown> },
        payload: ChatStreamPayload,
    ) => void;
    onToolCallEnd?: (
        info: { tool_call_id: string; ok: boolean; error: string | null },
        payload: ChatStreamPayload,
    ) => void;
    onDone?: (finalContent: string, payload: ChatStreamPayload) => void;
    onError?: (message: string, payload: ChatStreamPayload) => void;
}

interface RunState {
    handlers: ChatStreamHandlers;
    nextSeq: number;
    buffer: Map<number, ChatStreamPayload>;
    closed: boolean;
}

export class ChatStreamRouter {
    private runs = new Map<string, RunState>();

    subscribe(runId: string, handlers: ChatStreamHandlers): () => void {
        this.runs.set(runId, { handlers, nextSeq: 0, buffer: new Map(), closed: false });
        return () => this.runs.delete(runId);
    }

    handle(event: MessageEvent): void {
        let payload: ChatStreamPayload;
        try {
            payload = JSON.parse(event.data) as ChatStreamPayload;
        } catch {
            return;
        }
        const state = this.runs.get(payload.run_id);
        if (!state || state.closed) {
            return;
        }
        state.buffer.set(payload.seq, payload);
        // Drain in-order so subscribers see seqs sequentially even when the
        // server (or transport) interleaves them out of order.
        while (state.buffer.has(state.nextSeq)) {
            const next = state.buffer.get(state.nextSeq)!;
            state.buffer.delete(state.nextSeq);
            state.nextSeq += 1;
            this.dispatch(state, next);
            if (state.closed) {
                break;
            }
        }
    }

    private dispatch(state: RunState, p: ChatStreamPayload): void {
        const h = state.handlers;
        switch (p.kind) {
            case "delta":
                h.onDelta?.(p.text as string, p);
                break;
            case "tool_call_start":
                h.onToolCallStart?.(
                    {
                        tool_name: p.tool_name as string,
                        tool_call_id: p.tool_call_id as string,
                        args: (p.args as Record<string, unknown>) || {},
                    },
                    p,
                );
                break;
            case "tool_call_end":
                h.onToolCallEnd?.(
                    {
                        tool_call_id: p.tool_call_id as string,
                        ok: p.ok as boolean,
                        error: (p.error as string | null) ?? null,
                    },
                    p,
                );
                break;
            case "done":
                state.closed = true;
                h.onDone?.((p.final_content as string) ?? "", p);
                break;
            case "error":
                state.closed = true;
                h.onError?.((p.message as string) ?? "unknown error", p);
                break;
        }
    }
}

export function useChatStream() {
    const router = new ChatStreamRouter();
    const { connect, disconnect } = useSSE((evt) => router.handle(evt), ["chat_event"]);
    connect();
    onScopeDispose(() => disconnect());
    return {
        subscribe: (runId: string, handlers: ChatStreamHandlers) => router.subscribe(runId, handlers),
    };
}
