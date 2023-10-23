<template>
    <BButton
        v-b-tooltip.hover.bottom
        :title="title"
        :data-workflow-run="id"
        class="workflow-run btn-sm btn-primary fa fa-play"
        @click.stop="executeWorkflow" />
</template>

<script>
import { BButton, VBTooltip } from "bootstrap-vue";

import { localize } from "@/utils/localization";

export default {
    components: {
        BButton,
    },
    directives: {
        VBTooltip,
    },
    props: {
        id: {
            type: String,
            required: true,
        },
        root: {
            type: String,
            required: true,
        },
    },
    computed: {
        title() {
            return localize("Run workflow");
        },
    },
    methods: {
        executeWorkflow() {
            this.$router.push(`/workflows/run?id=${this.id}`);
        },
    },
};
</script>
