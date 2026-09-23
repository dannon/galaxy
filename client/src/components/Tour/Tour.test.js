import { getLocalVue } from "@tests/vitest/helpers";
import { shallowMount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useServerMock } from "@/api/client/__mocks__";

import TourList from "./TourList.vue";

const { server, http } = useServerMock();

const localVue = getLocalVue();

vi.mock("app");

const mockTours = [
    {
        id: "intro-to-galaxy",
        name: "Intro to Galaxy",
        description: "Learn the basics",
        tags: ["beginner"],
    },
    {
        id: "advanced-analysis",
        name: "Advanced Analysis",
        description: "Deep dive into tools",
        tags: ["advanced"],
    },
];

describe("Tour", () => {
    let wrapper;

    beforeEach(async () => {
        server.use(http.get("/api/tours", ({ response }) => response(200).json(mockTours)));
        wrapper = shallowMount(TourList, {
            propsData: {},
            localVue,
        });
        await flushPromises();
    });

    it("test tours", async () => {
        expect(wrapper.findAll("[data-description='tour link']").length).toBe(2);
    });

    it("shows tour descriptions as text", async () => {
        server.use(
            http.get("/api/tours", ({ response }) =>
                response(200).json([{ id: "t", name: "T", description: "Uses <b>markup</b>", tags: [] }]),
            ),
        );
        const textWrapper = shallowMount(TourList, { propsData: {}, localVue });
        await flushPromises();
        expect(textWrapper.text()).toContain("Uses <b>markup</b>");
        expect(textWrapper.find("b").exists()).toBe(false);
    });
});
