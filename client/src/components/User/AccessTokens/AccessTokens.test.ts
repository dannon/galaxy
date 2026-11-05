import { getLocalVue } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import { describe, expect, it, vi } from "vitest";

import { useServerMock } from "@/api/client/__mocks__";

import AccessTokens from "./AccessTokens.vue";

vi.mock("@/app", () => ({
    getGalaxyInstance: () => ({ user: { id: "user-id" } }),
}));

const { server, http } = useServerMock();
const localVue = getLocalVue();

const existing = {
    id: "token-1",
    name: "laptop",
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
        expect(wrapper.text()).toContain("laptop");
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
