import { getLocalVue } from "@tests/vitest/helpers";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import { sanitizeHtml } from "@/directives/sanitizeHtml";

import TourStep from "./TourStep.vue";

describe("TourStep", () => {
    it("shows the title as text and renders the content through v-safe-html", () => {
        vi.mocked(sanitizeHtml).mockClear();
        const wrapper = mount(TourStep as object, {
            localVue: getLocalVue(),
            propsData: {
                step: { title: "Step <i>one</i>", content: "Click <b>Upload</b>" },
                isPlaying: false,
                isLast: false,
            },
        });

        expect(wrapper.find(".tour-title").text()).toBe("Step <i>one</i>");
        expect(wrapper.find(".tour-title i").exists()).toBe(false);
        expect(sanitizeHtml).toHaveBeenCalledWith("Click <b>Upload</b>", "default");
        expect(wrapper.find(".tour-content b").text()).toBe("Upload");
    });
});
