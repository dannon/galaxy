import { defineStore } from "pinia";
import { computed, ref } from "vue";

import type { DatasetCollectionAttributes } from "@/api";
import { fetchCollectionAttributes } from "@/api/datasetCollections";

export const useCollectionAttributesStore = defineStore("collectionAttributesStore", () => {
    const storedAttributes = ref<{ [key: string]: DatasetCollectionAttributes }>({});
    const loadingAttributes = ref<{ [key: string]: boolean }>({});

    const getAttributes = computed(() => {
        return (hdcaId: string) => {
            if (!storedAttributes.value[hdcaId]) {
                storedAttributes.value[hdcaId] = {} as any; // TODO: Fix default type value here
                fetchAttributes({ hdcaId });
            }
            return storedAttributes.value[hdcaId];
        };
    });

    const isLoadingAttributes = computed(() => {
        return (hdcaId: string) => {
            return loadingAttributes.value[hdcaId] ?? false;
        };
    });

    async function fetchAttributes(params: { hdcaId: string }) {
        loadingAttributes.value[params.hdcaId] = true;
        try {
            const attributes = await fetchCollectionAttributes(params);
            storedAttributes.value[params.hdcaId] = attributes;
            return attributes;
        } finally {
            delete loadingAttributes.value[params.hdcaId];
        }
    }

    return {
        storedAttributes,
        getAttributes,
        isLoadingAttributes,
    };
});
