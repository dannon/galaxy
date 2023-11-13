import { defineStore } from "pinia";
import { computed, ref } from "vue";

import type { DatasetDetails, DatasetEntry, HistoryContentItemBase } from "@/api";
import { fetchDatasetDetails } from "@/api/datasets";

export const useDatasetStore = defineStore("datasetStore", () => {
    const storedDatasets = ref<{ [key: string]: DatasetDetails }>({});
    const loadingDatasets = ref<{ [key: string]: boolean }>({});

    const getDataset = computed(() => {
        return (datasetId: string) => {
            const dataset = storedDatasets.value[datasetId];
            if (needsUpdate(dataset)) {
                fetchDataset({ id: datasetId });
            }
            return dataset ?? null;
        };
    });

    const isLoadingDataset = computed(() => {
        return (datasetId: string) => {
            return loadingDatasets.value[datasetId] ?? false;
        };
    });

    async function fetchDataset(params: { id: string }) {
        loadingDatasets.value[params.id] = true;
        try {
            const dataset = await fetchDatasetDetails(params);
            storedDatasets.value[dataset.id] = dataset;
            return dataset;
        } finally {
            delete loadingDatasets.value[params.id];
        }
    }

    function saveDatasets(historyContentsPayload: HistoryContentItemBase[]) {
        const datasetList = historyContentsPayload.filter(
            (entry) => entry.history_content_type === "dataset"
        ) as DatasetDetails[];
        for (const dataset of datasetList) {
            storedDatasets.value[dataset.id] = dataset;
        }
    }

    function needsUpdate(dataset?: DatasetEntry) {
        if (!dataset) {
            return true;
        }
        const isNotDetailed = !("peek" in dataset);
        return isNotDetailed;
    }

    return {
        storedDatasets,
        getDataset,
        isLoadingDataset,
        fetchDataset,
        saveDatasets,
    };
});
