import Vue from "vue";
import { mount } from "@vue/test-utils";
import SavedRulesSelector from "components/RuleBuilder/SavedRulesSelector";
import { getNewAttachNode } from "jest/helpers";

describe("SavedRulesSelector", () => {
    let wrapper;
    let emitted;

    beforeEach(async () => {
        wrapper = mount(SavedRulesSelector, {
            propsData: {
                // Add a unique prefix for this test run so the test is not affected by local storage values
                prefix: "test_prefix_" + new Date().toISOString() + "_",
            },
            attachTo: getNewAttachNode(),
        });
        await Vue.nextTick();
    });

    afterEach(async () => {
        wrapper.savedRules = [];

        await Vue.nextTick();
    });

    it("disables history icon if there is no history", async () => {
        // Expect button to be disabled if we haven't saved a session
        expect(wrapper.find("#savedRulesButton").classes()).toContain("disabled");
    });

    it("should emit a click event when a session is clicked", async () => {
        wrapper.setProps({ user: "test_user" });
        const testRules = {
            rules: [
                {
                    type: "add_filter_count",
                    count: 1,
                    which: "first",
                    invert: false,
                },
            ],
            mapping: [
                {
                    type: "url",
                    columns: [0],
                },
            ],
        };

        wrapper.vm.saveSession(testRules);
        await Vue.nextTick();
        const sessions = wrapper.findAll("div.dropdown-menu > a.saved-rule-item");
        expect(sessions.length > 0).toBeTruthy();
        sessions.wrappers[0].trigger("click");
        emitted = wrapper.emitted();
        expect(emitted["update-rules"]).toBeTruthy();
    });
});
