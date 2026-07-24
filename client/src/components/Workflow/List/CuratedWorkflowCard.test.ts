import { createTestingPinia } from "@pinia/testing";
import { getFakeRegisteredUser } from "@tests/test-data";
import { getLocalVue, suppressBootstrapVueWarnings } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import { setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VueRouter from "vue-router";

import type { AnonymousUser } from "@/api";
import type { CuratedWorkflow } from "@/api/curatedWorkflows";
import { Toast } from "@/composables/toast";
import { useUserStore } from "@/stores/userStore";

import CuratedWorkflowCard from "./CuratedWorkflowCard.vue";
import GCard from "@/components/Common/GCard.vue";

const importTrsTool = vi.fn();
const copyWorkflow = vi.fn();
const routerPush = vi.fn();

vi.mock("@/components/Workflow/services", () => ({
    Services: class MockServices {
        importTrsTool(...args: unknown[]) {
            return importTrsTool(...args);
        }
    },
}));

vi.mock("@/components/Workflow/workflows.services", () => ({
    copyWorkflow: (...args: unknown[]) => copyWorkflow(...args),
}));

vi.mock("@/components/Workflow/redirectPath", () => ({
    getRedirectOnImportPath: () => "/workflows/list",
}));

let toastError: ReturnType<typeof vi.spyOn>;

const localVue = getLocalVue();
localVue.use(VueRouter);
const router = new VueRouter();

const FAKE_USER = getFakeRegisteredUser();
const ANONYMOUS_USER = {
    isAnonymous: true,
    total_disk_usage: 0,
    nice_total_disk_usage: "0 bytes",
} as AnonymousUser;

function iwcWorkflow(overrides: Partial<CuratedWorkflow> = {}): CuratedWorkflow {
    return {
        id: "velocyto-velocyto-on10x-filtered-barcodes",
        name: "Velocyto on 10x filtered barcodes",
        description: "A curated single cell workflow",
        tags: ["single-cell"],
        collections: ["Single Cell"],
        number_of_steps: 5,
        update_time: "2026-01-01T00:00:00",
        release: "0.1",
        doi: "10.5281/zenodo.1234567",
        external_url: "https://iwc.galaxyproject.org/workflow/velocyto/",
        owner: null,
        stored_workflow_id: null,
        trs_server: "dockstore",
        trs_tool_id: "#workflow/github.com/iwc-workflows/velocyto/main",
        trs_version_id: "main",
        ...overrides,
    };
}

function localWorkflow(overrides: Partial<CuratedWorkflow> = {}): CuratedWorkflow {
    return iwcWorkflow({
        id: "f2db41e1fa331b3e",
        name: "Locally curated workflow",
        stored_workflow_id: "f2db41e1fa331b3e",
        owner: "curator",
        external_url: null,
        doi: null,
        release: null,
        trs_server: null,
        trs_tool_id: null,
        trs_version_id: null,
        ...overrides,
    });
}

function mountCard(workflow: CuratedWorkflow, isAnonymous = false) {
    const pinia = createTestingPinia({ createSpy: vi.fn, stubActions: false });
    setActivePinia(pinia);

    const userStore = useUserStore();
    // An anonymous user is a non-null object without an email, not a null user.
    userStore.currentUser = isAnonymous ? ANONYMOUS_USER : { ...FAKE_USER };

    const wrapper = mount(CuratedWorkflowCard as object, {
        propsData: { workflow },
        localVue,
        router,
        pinia,
    });
    // Stub navigation so the assertions can see it; a real push would also warn
    // about redundant routes across repeated mounts.
    (wrapper.vm as unknown as { $router: { push: unknown } }).$router.push = routerPush;
    return wrapper;
}

function actionIds(wrapper: ReturnType<typeof mountCard>): string[] {
    const card = wrapper.findComponent(GCard);
    const actions = [...(card.props("primaryActions") ?? []), ...(card.props("extraActions") ?? [])];
    return actions.map((action: { id: string }) => action.id);
}

describe("CuratedWorkflowCard", () => {
    beforeEach(() => {
        suppressBootstrapVueWarnings();
        importTrsTool.mockReset();
        copyWorkflow.mockReset();
        routerPush.mockReset();
        toastError = vi.spyOn(Toast, "error").mockImplementation(() => {});
    });

    it("passes the title as a plain string so no preview modal can open", () => {
        // GCard only renders the clickable preview link when `title` is an object.
        // A catalog row's id is an IWC slug, so opening that modal would 404.
        const wrapper = mountCard(iwcWorkflow());

        expect(wrapper.findComponent(GCard).props("title")).toBe("Velocyto on 10x filtered barcodes");
        expect(typeof wrapper.findComponent(GCard).props("title")).toBe("string");
    });

    it("marks the card so Selenium's workflow-card count is unaffected", () => {
        const wrapper = mountCard(iwcWorkflow());

        expect(wrapper.classes()).toContain("curated-workflow-card");
        expect(wrapper.classes()).not.toContain("workflow-card");
    });

    it("offers import and external links for a catalog row", () => {
        const ids = actionIds(mountCard(iwcWorkflow()));

        expect(ids).toContain("curated-import");
        expect(ids).toContain("curated-external-link");
        expect(ids).toContain("curated-doi");
        expect(ids).not.toContain("curated-run");
    });

    it("offers run and view for a workflow hosted on this Galaxy", () => {
        const ids = actionIds(mountCard(localWorkflow()));

        expect(ids).toContain("curated-run");
        expect(ids).toContain("curated-import");
        expect(ids).toContain("curated-open");
        expect(ids).not.toContain("curated-external-link");
    });

    it("enables import for a registered user", () => {
        // Without this the handler tests below would still pass if the action
        // were disabled and therefore unclickable.
        const card = mountCard(iwcWorkflow()).findComponent(GCard);
        const importAction = card.props("primaryActions").find((a: { id: string }) => a.id === "curated-import");

        expect(importAction.disabled).toBe(false);
    });

    it("disables import for a catalog row with no TRS id to import", () => {
        const card = mountCard(iwcWorkflow({ trs_tool_id: null })).findComponent(GCard);
        const importAction = card.props("primaryActions").find((a: { id: string }) => a.id === "curated-import");

        expect(importAction.disabled).toBe(true);
    });

    it("disables import for anonymous users", () => {
        const card = mountCard(iwcWorkflow(), true).findComponent(GCard);
        const importAction = card.props("primaryActions").find((a: { id: string }) => a.id === "curated-import");

        expect(importAction.disabled).toBe(true);
        expect(importAction.title).toBe("Log in to import this workflow");
    });

    it("imports a catalog row through TRS with the values the server supplied", async () => {
        importTrsTool.mockResolvedValue({ id: "abc123", message: "Imported", status: "ok" });
        const wrapper = mountCard(iwcWorkflow());
        const card = wrapper.findComponent(GCard);
        const importAction = card.props("primaryActions").find((a: { id: string }) => a.id === "curated-import");

        await importAction.handler();
        await flushPromises();

        expect(importTrsTool).toHaveBeenCalledWith(
            "dockstore",
            "#workflow/github.com/iwc-workflows/velocyto/main",
            "main",
        );
        expect(copyWorkflow).not.toHaveBeenCalled();
        // Without this the test passes even when the import throws and is swallowed.
        expect(routerPush).toHaveBeenCalledWith("/workflows/list");
        expect(toastError).not.toHaveBeenCalled();
    });

    it("surfaces a failed TRS import instead of navigating", async () => {
        importTrsTool.mockRejectedValue(new Error("dockstore is down"));
        const wrapper = mountCard(iwcWorkflow());
        const card = wrapper.findComponent(GCard);
        const importAction = card.props("primaryActions").find((a: { id: string }) => a.id === "curated-import");

        await importAction.handler();
        await flushPromises();

        expect(routerPush).not.toHaveBeenCalled();
        expect(toastError).toHaveBeenCalled();
    });

    it("copies a locally hosted workflow instead of importing it through TRS", async () => {
        const wrapper = mountCard(localWorkflow());
        const card = wrapper.findComponent(GCard);
        const importAction = card.props("primaryActions").find((a: { id: string }) => a.id === "curated-import");

        await importAction.handler();
        await flushPromises();

        expect(copyWorkflow).toHaveBeenCalledWith("f2db41e1fa331b3e", "curator");
        expect(importTrsTool).not.toHaveBeenCalled();
    });
});
