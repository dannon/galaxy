import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { defineComponent, nextTick } from "vue";

import { sanitizeHtml } from "@/directives/sanitizeHtml";

import type { SafeHtmlBinding } from "./vSafeHtml";

// The directive is registered globally by the test setup, with sanitizeHtml
// replaced by a pass-through spy; here it is swapped for a marker so the tests
// can tell sanitized output apart from the raw input.
const sanitizeSpy = vi.mocked(sanitizeHtml);

function mountWith(value: SafeHtmlBinding) {
    const Host = defineComponent({
        props: {
            value: { type: [String, Object], default: null },
            other: { type: Number, default: 0 },
        },
        template: `<div><span data-other>{{ other }}</span><div class="target" v-safe-html="value" /></div>`,
    });
    return mount(Host as object, { propsData: { value } });
}

describe("v-safe-html", () => {
    beforeEach(() => {
        sanitizeSpy.mockReset();
        sanitizeSpy.mockImplementation((html) => `<i>sanitized:${html}</i>`);
    });

    test("renders the sanitizer output for a string with the default profile", () => {
        const wrapper = mountWith("<b>hello</b>");
        expect(sanitizeSpy).toHaveBeenCalledWith("<b>hello</b>", "default");
        expect(wrapper.find(".target").element.innerHTML).toBe("<i>sanitized:<b>hello</b></i>");
    });

    test("passes a named profile through", () => {
        const wrapper = mountWith({ html: "<a>x</a>", profile: "links" });
        expect(sanitizeSpy).toHaveBeenCalledWith("<a>x</a>", "links");
        expect(wrapper.find(".target").element.innerHTML).toBe("<i>sanitized:<a>x</a></i>");
    });

    test("treats null, undefined and a missing html field as empty", () => {
        mountWith(null);
        mountWith(undefined);
        mountWith({ html: null });
        expect(sanitizeSpy.mock.calls).toEqual([
            ["", "default"],
            ["", "default"],
            ["", "default"],
        ]);
    });

    test("re-sanitizes when the bound value changes", async () => {
        const wrapper = mountWith("first");
        await wrapper.setProps({ value: "second" });
        expect(sanitizeSpy).toHaveBeenLastCalledWith("second", "default");
        expect(wrapper.find(".target").element.innerHTML).toBe("<i>sanitized:second</i>");

        await wrapper.setProps({ value: { html: "second", profile: "links" } });
        expect(sanitizeSpy).toHaveBeenLastCalledWith("second", "links");
    });

    test("leaves the DOM alone when an unrelated re-render keeps the same content", async () => {
        const wrapper = mountWith({ html: "same", profile: "links" });
        const target = wrapper.find(".target").element;
        target.querySelector("i")!.setAttribute("data-decorated", "yes");
        sanitizeSpy.mockClear();

        await wrapper.setProps({ other: 1, value: { html: "same", profile: "links" } });
        await nextTick();

        expect(wrapper.find("[data-other]").text()).toBe("1");
        expect(sanitizeSpy).not.toHaveBeenCalled();
        expect(target.querySelector("i")!.getAttribute("data-decorated")).toBe("yes");
    });
});
