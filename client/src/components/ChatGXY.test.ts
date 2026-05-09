import { createTestingPinia } from "@pinia/testing";
import { getLocalVue } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setMockConfig } from "@/composables/__mocks__/config";

import ChatGXY from "./ChatGXY.vue";

vi.mock("@/composables/config");

// Capture the handlers passed into useChatStream().subscribe so the test can
// drive delta/done callbacks manually.
const subscribeMock = vi.fn();
vi.mock("@/composables/useChatStream", () => ({
    useChatStream: () => ({
        subscribe: subscribeMock,
    }),
}));

// In-memory mock for GalaxyApi -- the streaming path POSTs to /api/chat,
// the GET on mount fetches history, and DELETE/PUT cover feedback/cleanup.
const postMock = vi.fn();
const getMock = vi.fn();
const deleteMock = vi.fn();
const putMock = vi.fn();

vi.mock("@/api", () => ({
    GalaxyApi: () => ({
        POST: postMock,
        GET: getMock,
        DELETE: deleteMock,
        PUT: putMock,
    }),
}));

vi.mock("@/app", () => ({
    getGalaxyInstance: () => ({ frame: { add: vi.fn() } }),
}));

const localVue = getLocalVue();

function mountChatGXY() {
    const pinia = createTestingPinia({ createSpy: vi.fn });
    return mount(ChatGXY as any, {
        localVue,
        pinia,
        stubs: {
            FontAwesomeIcon: true,
            BSkeleton: true,
            Heading: true,
        },
    });
}

describe("ChatGXY streaming submit", () => {
    beforeEach(() => {
        subscribeMock.mockReset();
        postMock.mockReset();
        getMock.mockReset();
        deleteMock.mockReset();
        putMock.mockReset();

        // No prior history -> mount renders the welcome message and stops.
        getMock.mockResolvedValue({ data: [], error: null });
        deleteMock.mockResolvedValue({ data: undefined, error: null });
        putMock.mockResolvedValue({ data: undefined, error: null });

        setMockConfig({ enable_chat_streaming: true });
    });

    it("subscribes to the streaming run and accumulates deltas into the placeholder", async () => {
        postMock.mockResolvedValue({
            data: { streaming: true, run_id: "run-1", exchange_id: "exch-1" },
            error: null,
        });

        const wrapper = mountChatGXY();
        await flushPromises();

        const textarea = wrapper.find("textarea");
        await textarea.setValue("hello");
        await wrapper.find(".send-button").trigger("click");
        await flushPromises();

        // POST should fire with stream=true and the user's query in the body.
        expect(postMock).toHaveBeenCalledTimes(1);
        const postArgs = postMock.mock.calls[0]!;
        expect(postArgs[0]).toBe("/api/chat");
        expect(postArgs[1].params.query.stream).toBe(true);
        expect(postArgs[1].body.query).toBe("hello");

        // useChatStream().subscribe should be invoked with the returned run_id
        // and a handlers object containing the streaming callbacks.
        expect(subscribeMock).toHaveBeenCalledTimes(1);
        const [runId, handlers] = subscribeMock.mock.calls[0]!;
        expect(runId).toBe("run-1");
        expect(typeof handlers.onDelta).toBe("function");
        expect(typeof handlers.onDone).toBe("function");

        // Drive two delta events through the captured handler -- the placeholder
        // assistant message should accumulate the concatenated text.
        handlers.onDelta("Hi ", { seq: 0 });
        handlers.onDelta("there!", { seq: 1 });
        await flushPromises();

        expect(wrapper.text()).toContain("Hi there!");

        // While streaming, the typing caret is rendered and the input remains busy.
        expect(wrapper.find(".typing-caret").exists()).toBe(true);
        expect((wrapper.find("textarea").element as HTMLTextAreaElement).disabled).toBe(true);

        // onDone finalizes the placeholder: caret goes away and busy clears.
        handlers.onDone("Hi there!", { seq: 2 });
        await flushPromises();

        expect(wrapper.find(".typing-caret").exists()).toBe(false);
        expect((wrapper.find("textarea").element as HTMLTextAreaElement).disabled).toBe(false);
        expect(wrapper.text()).toContain("Hi there!");
    });
});
