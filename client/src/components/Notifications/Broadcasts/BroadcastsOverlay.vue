<script setup lang="ts">
import { storeToRefs } from "pinia";

import { useBroadcastsStore } from "@/stores/broadcastsStore";

import BroadcastContainer from "./BroadcastContainer.vue";

const { activeBroadcasts } = storeToRefs(useBroadcastsStore());
</script>

<template>
    <div v-if="activeBroadcasts.length > 0">
        <BroadcastContainer :options="{ broadcasts: activeBroadcasts }" />
    </div>
</template>
