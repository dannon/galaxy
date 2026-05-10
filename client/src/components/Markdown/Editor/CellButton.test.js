import { getLocalVue } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import Target from "./CellButton.vue";

const localVue = getLocalVue();

function mountTarget(props = {}) {
    return mount(Target, {
        localVue,
        propsData: props,
        stubs: {
            FontAwesomeIcon: true,
        },
    });
}

describe("CellButton.vue", () => {
    it("should render button", async () => {
        const wrapper = mountTarget({
            icon: "button-icon",
            title: "button-title",
        });
        expect(wrapper.find("[icon='button-icon']").exists()).toBeTruthy();
        expect(wrapper.attributes()["title"]).toBe("button-title");
        expect(wrapper.classes()).not.toContain("active");
        // outline-primary maps to color="blue" outline=true via variantToColor
        expect(wrapper.classes()).toContain("g-blue");
        expect(wrapper.classes()).toContain("g-outline");
        await wrapper.setProps({ active: true });
        expect(wrapper.classes()).toContain("active");
        // outline-secondary maps to outline=true (no color, defaults to grey)
        expect(wrapper.classes()).toContain("g-grey");
        expect(wrapper.classes()).toContain("g-outline");
        await wrapper.trigger("click");
        expect(wrapper.emitted("click")).toBeTruthy();
        expect(wrapper.emitted("click")?.length).toBe(1);
        wrapper.element.blur = vi.fn();
        await wrapper.trigger("mouseleave");
        expect(wrapper.element.blur).toHaveBeenCalled();
    });
});
