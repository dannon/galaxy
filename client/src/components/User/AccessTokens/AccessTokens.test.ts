import { getLocalVue } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import { describe, expect, it, vi } from "vitest";

import { useServerMock } from "@/api/client/__mocks__";

import AccessTokens from "./AccessTokens.vue";

vi.mock("@/app", () => ({
    getGalaxyInstance: () => ({ user: { id: "user-id" } }),
}));

const confirmMock = vi.fn();
vi.mock("@/composables/confirmDialog", () => ({
    useConfirmDialog: () => ({ confirm: confirmMock }),
}));

const { server, http } = useServerMock();
const localVue = getLocalVue();

const existing = {
    id: "token-1",
    name: "ci runner",
    token_hint: "abcdefgh",
    scopes: ["histories:read", "tools:run"],
    constraints: { surfaces: ["mcp"] },
    source: "ui",
    create_time: "2026-11-05T12:00:00",
    expires_at: "2027-02-03T12:00:00",
    last_used_at: null,
    revoked_at: null,
    active: true,
};

async function mountPage() {
    const wrapper = mount(AccessTokens as object, {
        global: { ...localVue, stubs: { ...localVue.stubs, BreadcrumbHeading: true, UtcDate: true } },
    });
    await flushPromises();
    return wrapper;
}

describe("AccessTokens.vue", () => {
    it("lists existing tokens with their limits", async () => {
        server.use(http.get("/api/users/{user_id}/tokens", ({ response }) => response(200).json([existing])));
        const wrapper = await mountPage();
        expect(wrapper.text()).toContain("ci runner");
        expect(wrapper.text()).toContain("gxy_abcdefgh");
        expect(wrapper.text()).toContain("mcp only");
    });

    it("creates a token and shows the secret once", async () => {
        let posted: any = null;
        server.use(
            http.get("/api/users/{user_id}/tokens", ({ response }) => response(200).json([])),
            http.post("/api/users/{user_id}/tokens", async ({ request, response }) => {
                posted = await request.json();
                return response(200).json({ ...existing, name: posted.name, token: "gxy_secretsecret" });
            }),
        );
        const wrapper = await mountPage();
        await wrapper.find("#access-token-name").setValue("Claude Code");
        await wrapper.find("#access-token-mcp-only").setValue(true);
        await wrapper.find("form").trigger("submit");
        await flushPromises();
        expect(posted.name).toBe("Claude Code");
        expect(posted.preset).toBe("agent");
        expect(posted.constraints).toEqual({ surfaces: ["mcp"] });
        expect(wrapper.text()).toContain("gxy_secretsecret");
        expect(wrapper.text()).toContain("will not be shown again");
    });

    it("hides the secret once dismissed and never shows it again", async () => {
        server.use(
            http.get("/api/users/{user_id}/tokens", ({ response }) => response(200).json([existing])),
            http.post("/api/users/{user_id}/tokens", ({ response }) =>
                response(200).json({ ...existing, token: "gxy_secretsecret" }),
            ),
        );
        const wrapper = await mountPage();
        await wrapper.find("#access-token-name").setValue("laptop");
        await wrapper.find("form").trigger("submit");
        await flushPromises();
        expect(wrapper.text()).toContain("gxy_secretsecret");
        await wrapper.find("[data-description='created token'] button.close").trigger("click");
        await flushPromises();
        expect(wrapper.text()).not.toContain("gxy_secretsecret");
        // the list only ever carries the hint
        expect(wrapper.text()).toContain("gxy_abcdefgh");
        const remounted = await mountPage();
        expect(remounted.text()).not.toContain("gxy_secretsecret");
    });

    it("submits once however many times the form fires", async () => {
        let posts = 0;
        server.use(
            http.get("/api/users/{user_id}/tokens", ({ response }) => response(200).json([])),
            http.post("/api/users/{user_id}/tokens", async ({ response }) => {
                posts += 1;
                await new Promise((resolve) => setTimeout(resolve, 20));
                return response(200).json({ ...existing, token: "gxy_secretsecret" });
            }),
        );
        const wrapper = await mountPage();
        // nothing to submit yet
        await wrapper.find("form").trigger("submit");
        await flushPromises();
        expect(posts).toBe(0);
        await wrapper.find("#access-token-name").setValue("laptop");
        await wrapper.find("form").trigger("submit");
        await wrapper.find("form").trigger("submit");
        await flushPromises();
        await new Promise((resolve) => setTimeout(resolve, 40));
        await flushPromises();
        expect(posts).toBe(1);
        expect(wrapper.text()).toContain("gxy_secretsecret");
    });

    it("revokes a token after confirmation", async () => {
        let deleted: string | null = null;
        let listed = [existing];
        server.use(
            http.get("/api/users/{user_id}/tokens", ({ response }) => response(200).json(listed)),
            http.delete("/api/users/{user_id}/tokens/{token_id}", ({ params, response }) => {
                deleted = params.token_id;
                listed = [];
                return response(200).json({ ...existing, revoked_at: "2026-11-06T12:00:00", active: false });
            }),
        );
        const wrapper = await mountPage();
        confirmMock.mockResolvedValueOnce(false);
        await wrapper.find("[data-description='revoke token']").trigger("click");
        await flushPromises();
        expect(deleted).toBeNull();
        expect(wrapper.text()).toContain("ci runner");
        confirmMock.mockResolvedValueOnce(true);
        await wrapper.find("[data-description='revoke token']").trigger("click");
        await flushPromises();
        expect(deleted).toBe("token-1");
        expect(wrapper.text()).not.toContain("ci runner");
        expect(wrapper.text()).toContain("No access tokens yet");
    });

    it("shows the error when loading fails", async () => {
        server.use(
            http.get("/api/users/{user_id}/tokens", ({ response }) =>
                response("4XX").json({ err_msg: "nope", err_code: 403 }, { status: 403 }),
            ),
        );
        const wrapper = await mountPage();
        expect(wrapper.text()).toContain("nope");
    });
});
