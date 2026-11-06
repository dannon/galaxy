<template>
    <ToolCard
        v-if="hasData"
        :id="configForm.id"
        :version="configForm.version"
        :title="configForm.name"
        :description="configForm.description"
        :options="configForm"
        :message-text="messageText"
        :message-variant="messageVariant"
        allow-editing-credentials
        @onChangeVersion="onChangeVersion"
        @onUpdateFavorites="onUpdateFavorites">
        <FormElement
            id="__label"
            :value="label"
            title="Label"
            help="Add a step label."
            :error="uniqueErrorLabel"
            @input="onLabel" />
        <FormElement
            id="__annotation"
            :value="annotation"
            title="Step Annotation"
            :area="true"
            help="Add an annotation or notes to this step. Annotations are available when a workflow is viewed."
            @input="onAnnotation" />
        <FormConditional :step="step" @onUpdateStep="(id, step) => $emit('onUpdateStep', id, step)" />
        <div v-if="canEditTool" class="d-flex justify-content-end mt-2">
            <GButton
                outline
                color="blue"
                size="small"
                :pressed="editingTool"
                tooltip
                title="Edit this custom tool's source and save it as a new revision"
                data-description="edit custom tool source"
                @click="editingTool = !editingTool">
                <FontAwesomeIcon :icon="faEdit" fixed-width />
                {{ editingTool ? "Back to parameters" : "Edit tool" }}
            </GButton>
        </div>
        <div v-if="showToolSource" class="mt-2 mb-4 custom-tool-step-source">
            <Heading h2 separator bold size="sm"> Tool Source </Heading>
            <CustomToolSourceEditor
                :key="toolUuid"
                :tool-uuid="toolUuid"
                toolbar
                @saved="onToolSaved"
                @cancel="editingTool = false" />
        </div>
        <div v-else class="mt-2 mb-4">
            <Heading h2 separator bold size="sm"> Tool Parameters </Heading>
            <FormDisplay
                :id="id"
                :key="formKey"
                :inputs="inputs"
                :errors="errors"
                text-enable="Set in Advance"
                text-disable="Set at Runtime"
                :workflow-building-mode="true"
                @onChange="onChange" />
        </div>
        <div v-if="!showToolSource" class="mt-2 mb-4">
            <Heading h2 separator bold size="sm"> Additional Options </Heading>
            <FormSection
                :id="stepId"
                :key="formKey"
                :node-inputs="stepInputs"
                :node-outputs="stepOutputs"
                :step="step"
                :datatypes="datatypes"
                :post-job-actions="postJobActions"
                @onChange="onChangePostJobActions" />
        </div>
    </ToolCard>
</template>

<script>
import { faEdit } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { storeToRefs } from "pinia";
import { defineAsyncComponent, ref, toRef, watch } from "vue";

import { useWorkflowStores } from "@/composables/workflowStores";
import { useRefreshFromStore } from "@/stores/refreshFromStore";
import Utils from "@/utils/utils";

import { useStepProps } from "../composables/useStepProps";
import { useUniqueLabelError } from "../composables/useUniqueLabelError";

import FormConditional from "./FormConditional.vue";
import FormSection from "./FormSection.vue";
import GButton from "@/components/BaseComponents/GButton.vue";
import Heading from "@/components/Common/Heading.vue";
import FormDisplay from "@/components/Form/FormDisplay.vue";
import FormElement from "@/components/Form/FormElement.vue";
import ToolCard from "@/components/Tool/ToolCard.vue";

export default {
    components: {
        FormDisplay,
        ToolCard,
        FormElement,
        FormConditional,
        FormSection,
        FontAwesomeIcon,
        GButton,
        Heading,
        // Monaco and its workers only load when a custom tool is actually edited from a node.
        CustomToolSourceEditor: defineAsyncComponent(() => import("@/components/Tool/CustomToolSourceEditor.vue")),
    },
    props: {
        step: {
            // type Step from @/stores/workflowStepStore
            type: Object,
            required: true,
        },
        datatypes: {
            type: Array,
            required: true,
        },
        /** Offer to edit a custom tool's source; off where the form is shown read-only */
        allowToolEdit: {
            type: Boolean,
            default: false,
        },
        /** Open the custom tool's source as soon as the form mounts */
        editTool: {
            type: Boolean,
            default: false,
        },
    },
    emits: ["onSetData", "onUpdateStep", "onChangePostJobActions", "onAnnotation", "onLabel"],
    setup(props, { emit }) {
        const { stepId, annotation, label, stepInputs, stepOutputs, configForm, postJobActions } = useStepProps(
            toRef(props, "step"),
        );
        const { stepStore } = useWorkflowStores();
        const uniqueErrorLabel = useUniqueLabelError(stepStore, label);

        const { formKey } = storeToRefs(useRefreshFromStore());
        const mainValues = ref(null);

        watch(
            () => formKey.value,
            () => (mainValues.value = null),
        );

        return {
            stepId,
            annotation,
            label,
            stepInputs,
            stepOutputs,
            configForm,
            postJobActions,
            uniqueErrorLabel,
            formKey,
            mainValues,
            faEdit,
        };
    },
    data() {
        return {
            messageText: "",
            messageVariant: "success",
            editingTool: this.editTool,
        };
    },
    computed: {
        id() {
            // Make sure we compute a unique id. Local tools don't include the version in the id,
            // but updating tool form when switching tool versions requires that the id changes.
            // (see https://github.com/galaxyproject/galaxy/blob/f5e07b11f0996e75b2b6f27896b2301d8fa8717d/client/src/components/Form/FormDisplay.vue#L108)
            // A custom tool revision keeps both, so key on its uuid as well.
            return `${this.stepId}:${this.configForm.id}/${this.configForm.version}${this.toolUuid ? `/${this.toolUuid}` : ""}`;
        },
        toolUuid() {
            return this.step.tool_uuid || this.configForm.uuid || undefined;
        },
        isUserDefinedTool() {
            return Boolean(this.toolUuid);
        },
        canEditTool() {
            return this.allowToolEdit && this.isUserDefinedTool;
        },
        showToolSource() {
            // editTool comes from the route query, which can name any step.
            return this.editingTool && this.canEditTool;
        },
        toolCardId() {
            return `${this.stepId}`;
        },
        hasData() {
            return !!this.configForm?.id;
        },
        inputs() {
            // TODO: Refactor
            // This code contains a computed side-effect and prop mutation.
            // Both should be refactored
            const inputs = this.configForm.inputs;
            Utils.deepEach(inputs, (input) => {
                if (input.type) {
                    if (["data", "data_collection"].indexOf(input.type) != -1) {
                        const extensions = Array.isArray(input.extensions) ? Utils.textify(input.extensions) : "";
                        input.titleonly = true;
                        input.info = `Data input '${input.name}' (${extensions})`;
                        input.value = { __class__: "RuntimeValue" };
                    } else {
                        const isRules = input.type === "rules";
                        input.connectable = !isRules;
                        input.collapsible_value = isRules
                            ? undefined
                            : {
                                  __class__: "RuntimeValue",
                              };

                        input.is_workflow =
                            (input.options && input.options.length === 0) ||
                            ["integer", "float"].indexOf(input.type) != -1;

                        if (isRules) {
                            // Do not descend into the rule-builder payload; its nested
                            // mapping/rules entries carry a `.type` and would otherwise be
                            // stamped with UI-only metadata that leaks into tool_state.
                            return false;
                        }
                    }
                }
            });
            Utils.deepEach(inputs, (input) => {
                if (input.type === "rules") {
                    return false;
                }
                if (input.type === "conditional") {
                    input.connectable = false;
                    input.test_param.collapsible_value = undefined;
                }
            });
            return inputs;
        },
        errors() {
            return this.configForm.errors;
        },
    },
    methods: {
        onAnnotation(newAnnotation) {
            this.$emit("onAnnotation", this.stepId, newAnnotation);
        },
        onLabel(newLabel) {
            this.$emit("onLabel", this.stepId, newLabel);
        },
        /**
         * Change event is triggered on component creation and input changes.
         * @param { Object } values contains flat key-value pairs `prefixed-name=value`
         */
        onChange(values) {
            const initialRequest = this.mainValues === null;
            this.mainValues = values;
            if (!initialRequest) {
                this.postChanges();
            }
        },
        onChangePostJobActions(postJobActions) {
            this.$emit("onChangePostJobActions", this.stepId, postJobActions);
        },
        onChangeVersion(newVersion) {
            this.messageText = `Now you are using '${this.configForm.name}' version ${newVersion}.`;
            this.postChanges({ version: newVersion });
        },
        /**
         * The source editor saved a new revision of the custom tool: point this step at it. Same tool
         * id and version, new uuid, so the swap is the version change path with a different key.
         */
        onToolSaved(tool) {
            this.editingTool = false;
            const name = tool.representation?.name ?? this.configForm.name;
            this.messageText = `Now using the revision of '${name}' saved just now (${tool.uuid}).`;
            this.postChanges({ toolUuid: tool.uuid });
            // The tall source panel leaves the inspector scrolled; bring the message back into view.
            this.$nextTick(() => this.$el.scrollIntoView?.({ block: "start" }));
        },
        onUpdateFavorites(user, newFavorites) {
            user.preferences["favorites"] = newFavorites;
        },
        postChanges({ version: newVersion, toolUuid: newToolUuid } = {}) {
            const payload = Object.assign({}, this.mainValues);
            const options = this.configForm;
            const toolUuid = newToolUuid ?? this.toolUuid;
            let toolId = options.id;
            let toolVersion = options.version;
            if (newVersion) {
                toolId = toolId.replace(toolVersion, newVersion);
                toolVersion = newVersion;
            }
            this.$emit("onSetData", this.stepId, {
                tool_id: toolId,
                tool_uuid: toolUuid,
                tool_version: toolVersion,
                type: "tool",
                inputs: payload,
            });
        },
    },
};
</script>

<style scoped>
/* The inspector does not size its children, so give Monaco room to render. */
.custom-tool-step-source {
    display: flex;
    flex-direction: column;
    height: 60vh;
    min-height: 20rem;
}
</style>
