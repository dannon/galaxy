import { createTestingPinia } from "@pinia/testing";
import { getLocalVue } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import { HttpResponse, useServerMock } from "@/api/client/__mocks__";
import { useUnprivilegedToolStore } from "@/stores/unprivilegedToolStore";

import CustomToolSourceEditor from "./CustomToolSourceEditor.vue";

// Monaco cannot run in happy-dom; the editor becomes a textarea bound the same way.
vi.mock("@guolao/vue-monaco-editor", () => ({
    useMonaco: () => ({ unload: vi.fn(), monacoRef: ref(null) }),
    VueMonacoEditor: {
        name: "VueMonacoEditor",
        props: ["value"],
        model: { prop: "value", event: "input" },
        template: `<textarea class="monaco-stub" :value="value" @input="$emit('input', $event.target.value)" />`,
    },
}));
vi.mock("./monacoEnvironment", () => ({}));
vi.mock("./YamlJs", () => ({ setupMonaco: vi.fn().mockResolvedValue(() => undefined) }));

const localVue = getLocalVue();
const { server, http } = useServerMock();

const REVISION_A = {
    uuid: "revision-a",
    representation: { class: "GalaxyUserTool", id: "basecommand", name: "Base command tool", version: "1.0.0" },
};

function mountEditor(props: Record<string, unknown> = {}) {
    const pinia = createTestingPinia({ createSpy: vi.fn, stubActions: true });
    const wrapper = mount(CustomToolSourceEditor as object, {
        propsData: props,
        localVue,
        pinia,
    });
    return { wrapper, pinia };
}

describe("CustomToolSourceEditor", () => {
    it("loads the revision's source when given a tool uuid", async () => {
        server.use(
            http.untyped.get("/api/unprivileged_tools/:uuid", ({ params }) => {
                requestedUuid = params.uuid as string;
                return HttpResponse.json(REVISION_A);
            }),
        );
        let requestedUuid: string | undefined;
        const { wrapper } = mountEditor({ toolUuid: "revision-a" });
        const source = () => (wrapper.find("textarea.monaco-stub").element as HTMLTextAreaElement).value;
        await vi.waitFor(() => expect(source(), wrapper.text()).toContain("name: Base command tool"), {
            timeout: 2000,
        });
        expect(requestedUuid).toBe("revision-a");
        expect(source()).toContain("class: GalaxyUserTool");
    });

    it("saves the source as a new revision and reports it", async () => {
        let posted: any;
        server.use(
            http.untyped.get("/api/unprivileged_tools/:uuid", () => HttpResponse.json(REVISION_A)),
            http.post("/api/unprivileged_tools", async ({ request, response }) => {
                posted = await request.json();
                return response(200).json({ ...REVISION_A, uuid: "revision-b" } as any);
            }),
        );
        const { wrapper } = mountEditor({ toolUuid: "revision-a", toolbar: true });
        await flushPromises();
        const textarea = wrapper.find("textarea.monaco-stub");
        await textarea.setValue("class: GalaxyUserTool\nid: basecommand\nname: Edited tool\nversion: 1.0.0\n");
        await wrapper.find("[data-description='save custom tool revision']").trigger("click");
        await flushPromises();

        expect(posted.src).toBe("representation");
        expect(posted.representation.name).toBe("Edited tool");
        expect(wrapper.emitted("saved")?.[0]?.[0]).toMatchObject({ uuid: "revision-b" });
        expect(useUnprivilegedToolStore().load).toHaveBeenCalledWith(true);
    });

    it("posts one revision when save is called again while a save is in flight", async () => {
        const post = vi.fn();
        server.use(
            http.untyped.post("/api/unprivileged_tools", async () => {
                post();
                return HttpResponse.json({ ...REVISION_A, uuid: "revision-b" });
            }),
        );
        const { wrapper } = mountEditor();
        await wrapper.find("textarea.monaco-stub").setValue("class: GalaxyUserTool\nid: basecommand\n");
        // The route page saves through the exposed method, not the embedded toolbar.
        const editor = wrapper.vm as unknown as { save: () => Promise<void> };
        await Promise.all([editor.save(), editor.save()]);
        await flushPromises();

        expect(post).toHaveBeenCalledTimes(1);
        expect(wrapper.emitted("saved")).toHaveLength(1);
    });

    it("shows a parse error instead of posting invalid yaml", async () => {
        const post = vi.fn();
        server.use(
            http.post("/api/unprivileged_tools", ({ response }) => {
                post();
                return response(200).json(REVISION_A as any);
            }),
        );
        const { wrapper } = mountEditor({ toolbar: true });
        await wrapper.find("textarea.monaco-stub").setValue("name: [unterminated");
        await wrapper.find("[data-description='save custom tool revision']").trigger("click");
        await flushPromises();

        expect(post).not.toHaveBeenCalled();
        expect(wrapper.text()).toContain("not valid YAML");
        expect(wrapper.emitted("saved")).toBeUndefined();
    });
});
