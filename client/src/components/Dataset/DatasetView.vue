<script setup lang="ts">
import { BNav, BNavItem } from "bootstrap-vue";
import { computed, ref, watch } from "vue";

import { usePersistentToggle } from "@/composables/persistentToggle";
import { useDatasetStore } from "@/stores/datasetStore";
import { useDatatypeVisualizationsStore } from "@/stores/datatypeVisualizationsStore";

import DatasetError from "../DatasetInformation/DatasetError.vue";
import LoadingSpan from "../LoadingSpan.vue";
import DatasetState from "./DatasetState.vue";
import Heading from "@/components/Common/Heading.vue";
import DatasetAttributes from "@/components/DatasetInformation/DatasetAttributes.vue";
import DatasetDetails from "@/components/DatasetInformation/DatasetDetails.vue";
import VisualizationsList from "@/components/Visualizations/Index.vue";
import VisualizationFrame from "@/components/Visualizations/VisualizationFrame.vue";
import CenterFrame from "@/entry/analysis/modules/CenterFrame.vue";

const datasetStore = useDatasetStore();
const datatypeVisualizationsStore = useDatatypeVisualizationsStore();
const { toggled: headerCollapsed, toggle: toggleHeaderCollapse } = usePersistentToggle("dataset-header-collapsed");

interface Props {
    datasetId: string;
    tab?: "details" | "edit" | "error" | "preview" | "visualize";
}

const props = withDefaults(defineProps<Props>(), {
    tab: "preview",
});

const iframeLoading = ref(true);
const preferredVisualization = ref<string>();

const dataset = computed(() => datasetStore.getDataset(props.datasetId));
const headerState = computed(() => (headerCollapsed.value ? "closed" : "open"));
const isLoading = computed(() => datasetStore.isLoadingDataset(props.datasetId));
const showError = computed(
    () => dataset.value && (dataset.value.state === "error" || dataset.value.state === "failed_metadata")
);

// Check if the dataset has a preferred visualization by datatype
async function checkPreferredVisualization() {
    if (dataset.value && dataset.value.file_ext) {
        try {
            const mapping = await datatypeVisualizationsStore.getPreferredVisualizationForDatatype(
                dataset.value.file_ext
            );
            if (mapping) {
                preferredVisualization.value = mapping.visualization;
            } else {
                preferredVisualization.value = undefined;
            }
        } catch (error) {
            preferredVisualization.value = undefined;
        }
    } else {
        preferredVisualization.value = undefined;
    }
}

// Watch for changes to the dataset to check for preferred visualizations
watch(() => dataset.value?.file_ext, checkPreferredVisualization, { immediate: true });
</script>

<template>
    <LoadingSpan v-if="isLoading || !dataset" message="Loading dataset details" />
    <div v-else class="dataset-view d-flex flex-column h-100">
        <header :key="`dataset-header-${dataset.id}`" class="dataset-header flex-shrink-0">
            <div class="d-flex">
                <Heading
                    h1
                    separator
                    inline
                    size="lg"
                    class="flex-grow-1"
                    :collapse="headerState"
                    @click="toggleHeaderCollapse">
                    {{ dataset?.hid }}: <span class="font-weight-bold">{{ dataset?.name }}</span>
                    <span class="dataset-state-header">
                        <DatasetState :dataset-id="datasetId" />
                    </span>
                </Heading>
            </div>
            <transition v-if="dataset" name="header">
                <div v-show="headerState === 'open'" class="header-details">
                    <table class="dataset-metadata-table">
                        <tr v-if="dataset.file_size" class="metadata-row">
                            <td v-localize class="prompt">size</td>
                            <td class="value font-weight-bold">{{ dataset.file_size }}</td>
                        </tr>
                        <tr v-if="dataset.file_ext" class="metadata-row">
                            <td v-localize class="prompt">format</td>
                            <td class="value font-weight-bold">{{ dataset.file_ext }}</td>
                        </tr>
                        <tr v-if="dataset.genome_build" class="metadata-row">
                            <td v-localize class="prompt">database</td>
                            <td class="value">
                                <BLink
                                    class="font-weight-bold"
                                    data-label="Database/Build"
                                    :to="`/datasets/${datasetId}/edit`">
                                    {{ dataset.genome_build }}
                                </BLink>
                            </td>
                        </tr>
                    </table>
                </div>
            </transition>
        </header>
        <BNav pills class="my-2 p-2 bg-light border-bottom">
            <BNavItem title="Preview" :active="tab === 'preview'" :to="`/datasets/${datasetId}/preview`">
                Preview
            </BNavItem>
            <BNavItem
                v-if="!showError"
                title="Visualize"
                :active="tab === 'visualize'"
                :to="`/datasets/${datasetId}/visualize`">
                Visualize
            </BNavItem>
            <BNavItem title="Details" :active="tab === 'details'" :to="`/datasets/${datasetId}/details`">
                Details
            </BNavItem>
            <BNavItem title="Edit" :active="tab === 'edit'" :to="`/datasets/${datasetId}/edit`">Edit</BNavItem>
            <BNavItem v-if="showError" title="Error" :active="tab === 'error'" :to="`/datasets/${datasetId}/error`">
                Error
            </BNavItem>
        </BNav>
        <div v-if="tab === 'preview'" class="h-100">
            <VisualizationFrame
                v-if="preferredVisualization"
                :dataset-id="datasetId"
                :visualization="preferredVisualization"
                @load="iframeLoading = false" />
            <CenterFrame
                v-else
                :src="`/datasets/${datasetId}/display/?preview=True`"
                :is_preview="true"
                @load="iframeLoading = false" />
        </div>
        <div v-else-if="tab === 'visualize'" class="d-flex flex-column overflow-hidden overflow-y">
            <VisualizationsList :dataset-id="datasetId" />
        </div>
        <div v-else-if="tab === 'edit'" class="d-flex flex-column overflow-hidden overflow-y mt-2">
            <DatasetAttributes :dataset-id="datasetId" />
        </div>
        <div v-else-if="tab === 'details'" class="d-flex flex-column overflow-hidden overflow-y mt-2">
            <DatasetDetails :dataset-id="datasetId" />
        </div>
        <div v-else-if="tab === 'error'" class="d-flex flex-column overflow-hidden overflow-y mt-2">
            <DatasetError :dataset-id="datasetId" />
        </div>
    </div>
</template>

<style lang="scss" scoped>
@import "theme/blue.scss";

.header-details {
    padding-left: 1rem;
    max-height: 500px;
    opacity: 1;
    transition: all 0.25s ease;
    overflow: hidden;
}

.header-enter, /* change to header-enter-from with Vue 3 */
.header-leave-to {
    max-height: 0;
    margin-top: 0;
    padding-top: 0;
    padding-bottom: 0;
    opacity: 0;
}

.dataset-state-header {
    font-size: $h5-font-size;
    vertical-align: middle;
}

.dataset-metadata-table {
    border-spacing: 0;
    margin-top: 0.5rem;
}

.metadata-row {
    line-height: 1.8;
}

.prompt {
    color: $text-muted;
    font-size: 0.9rem;
    padding-right: 1rem;
    text-align: right;
    white-space: nowrap;
    &::after {
        content: ":";
    }
}

.value {
    padding-left: 0.25rem;
}
</style>
