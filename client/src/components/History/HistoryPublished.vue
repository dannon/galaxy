<script setup lang="ts">
import { onMounted, ref } from "vue";

import { historyFetcher } from "@/api/histories";

import HistoryView from "./HistoryView.vue";
import PublishedItem from "@/components/Common/PublishedItem.vue";

interface Props {
    id: string;
}

const props = defineProps<Props>();
const history = ref({});

onMounted(async () => {
    const { data } = await historyFetcher({ history_id: props.id });
    history.value = data;
});
</script>

<template>
    <PublishedItem :item="history">
        <template v-slot>
            <HistoryView :id="id" />
        </template>
    </PublishedItem>
</template>
