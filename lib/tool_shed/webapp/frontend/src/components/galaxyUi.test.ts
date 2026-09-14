import { GButton, GLink } from "@galaxyproject/galaxy-ui"
import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

/**
 * The galaxy-ui package ships raw source and is compiled by the Galaxy client on
 * Vue 2.7 and by this app on Vue 3. The client's own tests only ever exercise the
 * Vue 2.7 half, so behaviour that differs between the two -- listener fallthrough,
 * router entry points, attribute merging -- has no coverage there at all.
 *
 * These are the Vue 3 half of that contract, kept next to the first external
 * consumer that depends on it.
 */
describe("galaxy-ui under Vue 3", () => {
    it("emits one click per click", async () => {
        // Vue 3 merges listeners into $attrs, so a component that both spreads
        // $attrs onto its root and emits its own click hands the caller two
        // calls for one press.
        const onClick = vi.fn()
        const wrapper = mount(GButton, {
            props: { color: "blue" },
            attrs: { onClick },
            slots: { default: "Press" },
        })

        await wrapper.find("button").trigger("click")

        expect(onClick).toHaveBeenCalledTimes(1)
    })

    it("emits one click per click from GLink", async () => {
        const onClick = vi.fn()
        const wrapper = mount(GLink, {
            attrs: { onClick },
            slots: { default: "Go" },
        })

        await wrapper.find("button").trigger("click")

        expect(onClick).toHaveBeenCalledTimes(1)
    })

    it("does not click through when disabled", async () => {
        const onClick = vi.fn()
        const wrapper = mount(GButton, {
            props: { color: "blue", disabled: true },
            attrs: { onClick },
            slots: { default: "Press" },
        })

        await wrapper.find("button").trigger("click")

        expect(onClick).not.toHaveBeenCalled()
    })
})
