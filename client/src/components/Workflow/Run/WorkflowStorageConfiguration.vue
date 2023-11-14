<template>
    <span class="workflow-storage-indicators">
        <b-button
            v-bind="buttonProps"
            id="workflow-storage-indicator-primary"
            class="workflow-storage-indicator workflow-storage-indicator-primary"
            @click="showPreferredObjectStoreModal = true">
            <span class="fa fa-hdd" />
        </b-button>
        <WorkflowTargetPreferredObjectStorePopover
            target="workflow-storage-indicator-primary"
            :title-suffix="suffixPrimary"
            :invocation-preferred-object-store-id="selectedObjectStoreId">
        </WorkflowTargetPreferredObjectStorePopover>
        <b-modal
            v-bind="modalProps"
            v-model="showPreferredObjectStoreModal"
            title="Invocation Preferred Object Store"
            hide-footer>
            <WorkflowSelectPreferredObjectStore
                :invocation-preferred-object-store-id="selectedObjectStoreId"
                @updated="onUpdate" />
        </b-modal>
        <b-button
            v-if="splitObjectStore"
            v-bind="buttonProps"
            id="workflow-storage-indicator-intermediate"
            class="workflow-storage-indicator workflow-storage-indicator-intermediate"
            @click="showIntermediatePreferredObjectStoreModal = true">
            <span class="fa fa-hdd" />
        </b-button>
        <WorkflowTargetPreferredObjectStorePopover
            v-if="splitObjectStore"
            target="workflow-storage-indicator-intermediate"
            title-suffix=" (Intermediate Datasets)"
            :invocation-preferred-object-store-id="selectedIntermediateObjectStoreId">
        </WorkflowTargetPreferredObjectStorePopover>
        <b-modal
            v-bind="modalProps"
            v-model="showIntermediatePreferredObjectStoreModal"
            title="Invocation Preferred Object Store (Intermediate Datasets)"
            hide-footer>
            <WorkflowSelectPreferredObjectStore
                :invocation-preferred-object-store-id="selectedIntermediateObjectStoreId"
                @updated="onUpdateIntermediate" />
        </b-modal>
    </span>
</template>

<script>
import WorkflowSelectPreferredObjectStore from "./WorkflowSelectPreferredObjectStore";
import WorkflowTargetPreferredObjectStorePopover from "./WorkflowTargetPreferredObjectStorePopover";

export default {
    components: {
        WorkflowSelectPreferredObjectStore,
        WorkflowTargetPreferredObjectStorePopover,
    },
    props: {
        splitObjectStore: {
            type: Boolean,
            required: true,
        },
        invocationPreferredObjectStoreId: {
            type: String,
            default: null,
        },
        invocationPreferredIntermediateObjectStoreId: {
            type: String,
            default: null,
        },
    },
    data() {
        return {
            showPreferredObjectStoreModal: false,
            showIntermediatePreferredObjectStoreModal: false,
            selectedObjectStoreId: this.invocationPreferredObjectStoreId,
            selectedIntermediateObjectStoreId: this.invocationPreferredIntermediateObjectStoreId,
        };
    },
    computed: {
        suffixPrimary() {
            if (this.splitObjectStore) {
                return ` (Workflow Output Datasets)`;
            } else {
                return "";
            }
        },
        modalProps() {
            return {
                "modal-class": "invocation-preferred-object-store-modal",
                "title-tag": "h3",
                size: "sm",
            };
        },
        buttonProps() {
            return {
                // size: 'sm',
                role: "button",
                variant: "link",
            };
        },
    },
    methods: {
        async onUpdate(preferredObjectStoreId) {
            this.selectedObjectStoreId = preferredObjectStoreId;
            this.$emit("updated", preferredObjectStoreId, false);
            this.showPreferredObjectStoreModal = false;
        },
        async onUpdateIntermediate(preferredObjectStoreId) {
            this.selectedIntermediateObjectStoreId = preferredObjectStoreId;
            this.$emit("updated", preferredObjectStoreId, true);
            this.showIntermediatePreferredObjectStoreModal = false;
        },
    },
};
</script>
