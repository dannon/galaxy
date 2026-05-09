import { describe, expect, it, vi } from "vitest";

import { type ChatStreamPayload, ChatStreamRouter } from "@/composables/useChatStream";

function makeEvent(payload: ChatStreamPayload): MessageEvent {
    return new MessageEvent("chat_event", { data: JSON.stringify(payload) });
}

describe("ChatStreamRouter", () => {
    it("dispatches deltas and done to the matching run subscriber", () => {
        const router = new ChatStreamRouter();
        const onDelta = vi.fn();
        const onDone = vi.fn();
        router.subscribe("run-1", { onDelta, onDone });

        router.handle(makeEvent({ kind: "delta", run_id: "run-1", exchange_id: "e", seq: 0, text: "hi " }));
        router.handle(makeEvent({ kind: "delta", run_id: "run-1", exchange_id: "e", seq: 1, text: "there" }));
        router.handle(
            makeEvent({ kind: "done", run_id: "run-1", exchange_id: "e", seq: 2, final_content: "hi there" }),
        );

        expect(onDelta).toHaveBeenCalledTimes(2);
        expect(onDelta).toHaveBeenNthCalledWith(1, "hi ", expect.objectContaining({ seq: 0 }));
        expect(onDone).toHaveBeenCalledWith("hi there", expect.any(Object));
    });

    it("ignores events for unsubscribed run_ids", () => {
        const router = new ChatStreamRouter();
        const onDelta = vi.fn();
        router.subscribe("run-1", { onDelta });
        router.handle(makeEvent({ kind: "delta", run_id: "run-2", exchange_id: "e", seq: 0, text: "x" }));
        expect(onDelta).not.toHaveBeenCalled();
    });

    it("reorders out-of-order seqs within a run", () => {
        const router = new ChatStreamRouter();
        const seen: string[] = [];
        router.subscribe("run-1", { onDelta: (text) => seen.push(text) });
        router.handle(makeEvent({ kind: "delta", run_id: "run-1", exchange_id: "e", seq: 1, text: "B" }));
        router.handle(makeEvent({ kind: "delta", run_id: "run-1", exchange_id: "e", seq: 0, text: "A" }));
        router.handle(makeEvent({ kind: "delta", run_id: "run-1", exchange_id: "e", seq: 2, text: "C" }));
        expect(seen).toEqual(["A", "B", "C"]);
    });
});
