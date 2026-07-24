import { createTestingPinia } from "@pinia/testing";
import { getFakeRegisteredUser } from "@tests/test-data";
import { getLocalVue, suppressBootstrapVueWarnings } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import { setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VueRouter from "vue-router";

import { useServerMock } from "@/api/client/__mocks__";
import type { CuratedWorkflow, CuratedWorkflowsIndexResponse } from "@/api/curatedWorkflows";
import { useUserStore } from "@/stores/userStore";

import CuratedWorkflowList from "./CuratedWorkflowList.vue";

const { server, http } = useServerMock();

const localVue = getLocalVue();
localVue.use(VueRouter);
const router = new VueRouter();

const FAKE_USER = getFakeRegisteredUser();

/** Records any hit on the badge counts endpoint, which curated cards must never make. */
const countsRequested = vi.fn();

function iwcWorkflow(overrides: Partial<CuratedWorkflow> = {}): CuratedWorkflow {
    return {
        id: "iwc-workflow-1",
        name: "Velocyto on 10x filtered barcodes",
        description: "A curated single cell workflow",
        tags: ["single-cell"],
        collections: ["single-cell"],
        number_of_steps: 5,
        update_time: "2026-01-01T00:00:00",
        release: "0.1",
        doi: "10.5281/zenodo.1234567",
        external_url: "https://iwc.galaxyproject.org/workflow/iwc-workflow-1/",
        owner: null,
        stored_workflow_id: null,
        trs_server: "dockstore",
        trs_tool_id: "#workflow/github.com/iwc-workflows/velocyto/main",
        trs_version_id: "main",
        ...overrides,
    };
}

function localWorkflow(overrides: Partial<CuratedWorkflow> = {}): CuratedWorkflow {
    return {
        id: "f2db41e1fa331b3e",
        name: "Locally curated workflow",
        description: "Published on this Galaxy",
        tags: ["curated"],
        collections: [],
        number_of_steps: 3,
        update_time: "2026-01-02T00:00:00",
        release: null,
        doi: null,
        external_url: null,
        owner: "curator",
        stored_workflow_id: "f2db41e1fa331b3e",
        trs_server: null,
        trs_tool_id: null,
        trs_version_id: null,
        ...overrides,
    };
}

async function mountCuratedList(response: CuratedWorkflowsIndexResponse) {
    server.use(
        http.get("/api/workflows/curated", ({ response: respond }) => {
            return respond(200).json(response);
        }),
    );

    const pinia = createTestingPinia({ createSpy: vi.fn });
    setActivePinia(pinia);

    const userStore = useUserStore();
    userStore.currentUser = FAKE_USER;

    const wrapper = mount(CuratedWorkflowList as object, {
        localVue,
        pinia,
        router,
    });

    await flushPromises();

    return wrapper;
}

describe("CuratedWorkflowList", () => {
    beforeEach(() => {
        suppressBootstrapVueWarnings();
        vi.clearAllMocks();
        server.use(
            // WorkflowListTabs gates the curated tab on the config store, which
            // fetches eagerly when it is first instantiated.
            http.get("/api/configuration", ({ response }) => {
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                return response(200).json({ curated_workflows_enabled: true } as any);
            }),
            http.get("/api/workflows/{workflow_id}/counts", ({ response }) => {
                countsRequested();
                return response(200).json({});
            }),
        );
    });

    it("renders a card per workflow for the iwc source", async () => {
        const workflows = [
            iwcWorkflow(),
            iwcWorkflow({ id: "iwc-workflow-2", name: "Another curated workflow" }),
            iwcWorkflow({ id: "iwc-workflow-3", name: "A third curated workflow" }),
        ];
        const wrapper = await mountCuratedList({ source: "iwc", total_matches: 3, workflows });

        expect(wrapper.findAll(".curated-workflow-card")).toHaveLength(3);
        expect(wrapper.find("#curated-workflows-source-note").exists()).toBe(true);
        expect(wrapper.find("#curated-workflows-preparing").exists()).toBe(false);
        expect(wrapper.find("#curated-workflows-unavailable").exists()).toBe(false);
        expect(wrapper.find("#curated-workflows-empty").exists()).toBe(false);
    });

    it("offers import but not run for iwc rows, and never requests workflow counts", async () => {
        const workflow = iwcWorkflow();
        const wrapper = await mountCuratedList({ source: "iwc", total_matches: 1, workflows: [workflow] });

        expect(wrapper.find(`#g-card-action-curated-import-${workflow.id}`).exists()).toBe(true);
        expect(wrapper.find(`#g-card-action-curated-run-${workflow.id}`).exists()).toBe(false);

        // The curated card deliberately avoids the workflow card badge composables,
        // whose keyed cache getter fires this request as a side effect.
        expect(countsRequested).not.toHaveBeenCalled();
    });

    it("renders a card per workflow for the local source", async () => {
        const workflows = [localWorkflow(), localWorkflow({ id: "abc123", stored_workflow_id: "abc123" })];
        const wrapper = await mountCuratedList({ source: "local", total_matches: 2, workflows });

        expect(wrapper.findAll(".curated-workflow-card")).toHaveLength(2);
        expect(wrapper.find("#g-card-action-curated-run-f2db41e1fa331b3e").exists()).toBe(true);
        expect(countsRequested).not.toHaveBeenCalled();
    });

    it("renders the preparing alert and no cards while the catalog is being fetched", async () => {
        const wrapper = await mountCuratedList({
            source: "preparing",
            total_matches: 0,
            workflows: [],
            message: "Galaxy is fetching the curated workflow catalog.",
        });

        expect(wrapper.find("#curated-workflows-preparing").exists()).toBe(true);
        expect(wrapper.find("#curated-workflows-preparing").text()).toContain("fetching the curated workflow catalog");
        expect(wrapper.findAll(".curated-workflow-card")).toHaveLength(0);
        expect(wrapper.find("#curated-workflows-unavailable").exists()).toBe(false);
    });

    it("renders the unavailable alert and no cards when the catalog cannot be reached", async () => {
        const wrapper = await mountCuratedList({
            source: "unavailable",
            total_matches: 0,
            workflows: [],
            message: "Galaxy could not reach the curated workflow catalog.",
        });

        expect(wrapper.find("#curated-workflows-unavailable").exists()).toBe(true);
        expect(wrapper.find("#curated-workflows-unavailable").text()).toContain("could not reach");
        expect(wrapper.findAll(".curated-workflow-card")).toHaveLength(0);
        expect(wrapper.find("#curated-workflows-preparing").exists()).toBe(false);
    });

    it("renders the empty alert for an unfiltered local catalog with no workflows", async () => {
        const wrapper = await mountCuratedList({ source: "local", total_matches: 0, workflows: [] });

        expect(wrapper.find("#curated-workflows-empty").exists()).toBe(true);
        expect(wrapper.find("#no-curated-workflow-found").exists()).toBe(false);
        expect(wrapper.findAll(".curated-workflow-card")).toHaveLength(0);
    });
});
