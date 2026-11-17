import { getLocalVue } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useServerMock } from "@/api/client/__mocks__";
import type { components } from "@/api/schema";

import GA4GHPassport from "./GA4GHPassport.vue";

type PassportStatus = components["schemas"]["GA4GHPassportStatus"];

const replaceStateSpy = vi.spyOn(window.history, "replaceState");

const confirmMock = vi.fn();
vi.mock("@/composables/confirmDialog", () => ({
    useConfirmDialog: () => ({ confirm: confirmMock }),
}));

const localVue = getLocalVue(true);
const { server, http } = useServerMock();

const ISSUER = "https://ras.example.org";
const IN_AN_HOUR = new Date(Date.now() + 3600 * 1000).toISOString();
const IN_A_MINUTE = new Date(Date.now() + 60 * 1000).toISOString();

const IDENTITY = { issuer: ISSUER, sub: "alice-sub", preferred_username: "alice", bound_since: "2026-11-13T10:00:00Z" };

function passport(usableUntil = IN_AN_HOUR) {
    return {
        issuer: ISSUER,
        sub: "alice-sub",
        txn: "txn-1",
        issued_at: "2026-11-16T10:00:00Z",
        expires_at: IN_AN_HOUR,
        visas_expire_at: IN_AN_HOUR,
        usable_until: usableUntil,
        rejected_visas: 0,
        visas: [
            {
                type: "https://ras.nih.gov/visas/v1.1",
                issuer: ISSUER,
                recognized: true,
                expires_at: IN_AN_HOUR,
                grants: [
                    {
                        phs_id: "phs000001",
                        version: "v1",
                        participant_set: "p1",
                        consent_group: "c1",
                        consent_name: "General Research Use",
                        role: "pi",
                        authorization: "phs000001.c1",
                        expiration: "2027-11-16T00:00:00Z",
                    },
                ],
            },
        ],
    };
}

function serve(...statuses: PassportStatus[]) {
    const queue = [...statuses];
    server.use(
        http.get("/api/users/current/ga4gh_passport", ({ response }) => {
            return response(200).json(queue.length > 1 ? queue.shift()! : queue[0]!);
        }),
    );
}

async function mountComponent(props: Record<string, string> = {}) {
    const wrapper = mount(GA4GHPassport as object, {
        global: { ...localVue, stubs: { ...localVue.stubs, BreadcrumbHeading: true } },
        pinia: createPinia(),
        props,
    });
    await flushPromises();
    return wrapper;
}

describe("GA4GHPassport.vue", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("offers Connect when no identity is linked", async () => {
        serve({ enabled: true, identity: null, passport: null });
        const wrapper = await mountComponent();
        expect(wrapper.find(".ga4gh-passport-not-connected").exists()).toBe(true);
        expect(wrapper.text()).toContain("Connect");
        expect(wrapper.find(".ga4gh-passport-grants").exists()).toBe(false);
    });

    it("shows the linked identity and offers Reconnect when no Passport is stored", async () => {
        serve({ enabled: true, identity: IDENTITY, passport: null });
        const wrapper = await mountComponent();
        expect(wrapper.find(".ga4gh-passport-identity").text()).toContain("alice");
        expect(wrapper.find(".ga4gh-passport-missing").text()).toContain("Reconnect");
        expect(wrapper.find(".ga4gh-passport-forget").exists()).toBe(false);
    });

    it("lists the grants of a stored Passport", async () => {
        serve({ enabled: true, identity: IDENTITY, passport: passport() });
        const wrapper = await mountComponent();
        const grants = wrapper.find(".ga4gh-passport-grants");
        expect(grants.text()).toContain("phs000001.c1");
        expect(grants.text()).toContain("General Research Use");
        expect(wrapper.find(".text-danger").exists()).toBe(false);
    });

    it("warns when the Passport is about to become unusable", async () => {
        serve({ enabled: true, identity: IDENTITY, passport: passport(IN_A_MINUTE) });
        const wrapper = await mountComponent();
        expect(wrapper.find(".text-danger").text()).toContain("reconnect to renew it");
    });

    it("forgets the Passport after confirmation", async () => {
        serve(
            { enabled: true, identity: IDENTITY, passport: passport() },
            { enabled: true, identity: IDENTITY, passport: null },
        );
        const deleted = vi.fn();
        server.use(
            http.delete("/api/users/current/ga4gh_passport", ({ response }) => {
                deleted();
                return response(204).empty();
            }),
        );
        confirmMock.mockResolvedValue(true);
        const wrapper = await mountComponent();
        await wrapper.find(".ga4gh-passport-forget").trigger("click");
        await flushPromises();
        expect(confirmMock).toHaveBeenCalled();
        expect(deleted).toHaveBeenCalledOnce();
        expect(wrapper.find(".ga4gh-passport-missing").exists()).toBe(true);
    });

    it("keeps the Passport when Forget is cancelled", async () => {
        serve({ enabled: true, identity: IDENTITY, passport: passport() });
        const deleted = vi.fn();
        server.use(
            http.delete("/api/users/current/ga4gh_passport", ({ response }) => {
                deleted();
                return response(204).empty();
            }),
        );
        confirmMock.mockResolvedValue(false);
        const wrapper = await mountComponent();
        await wrapper.find(".ga4gh-passport-forget").trigger("click");
        await flushPromises();
        expect(deleted).not.toHaveBeenCalled();
        expect(wrapper.find(".ga4gh-passport-grants").exists()).toBe(true);
    });

    it("reports a successful connect and clears the query", async () => {
        serve({ enabled: true, identity: IDENTITY, passport: passport() });
        const wrapper = await mountComponent({ outcome: "connected" });
        expect(wrapper.find(".alert-success").text()).toContain("connected");
        expect(replaceStateSpy).toHaveBeenCalledWith(window.history.state, "", window.location.pathname);
    });

    it.each([
        ["access_denied", "You declined"],
        ["identity_mismatch", "linked to a different identity"],
        ["identity_in_use", "already linked to another Galaxy account"],
        ["server_error", "Connecting your Passport failed. (server_error)"],
    ])("explains connect error %s", async (code, text) => {
        serve({ enabled: true, identity: null, passport: null });
        const wrapper = await mountComponent({ error: code });
        expect(wrapper.find(".alert-danger").text()).toContain(text);
        expect(replaceStateSpy).toHaveBeenCalledWith(window.history.state, "", window.location.pathname);
    });
});
