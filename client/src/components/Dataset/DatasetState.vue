<script setup lang="ts">
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { computed } from "vue";

import { STATES } from "@/components/History/Content/model/states";
import { useDatasetStore } from "@/stores/datasetStore";

const datasetStore = useDatasetStore();

const props = defineProps<{
    datasetId: string;
}>();

const dataset = computed(() => datasetStore.getDataset(props.datasetId));

const contentState = computed(() => {
    return dataset.value && STATES[dataset.value.state] ? STATES[dataset.value.state] : null;
});

const contentCls = computed(() => {
    const status = contentState.value && contentState.value.status;
    if (!status) {
        return `alert-success`;
    } else {
        return `alert-${status}`;
    }
});
</script>

<template>
    <span v-if="dataset && contentState" class="rounded px-2 py-1 ml-2" :class="contentCls">
        <span v-if="contentState.icon" class="mr-1">
            <FontAwesomeIcon fixed-width :icon="contentState.icon" :spin="contentState.spin" />
        </span>
        {{ contentState.text || dataset.state || "n/a" }}
    </span>
</template>
