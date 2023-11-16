<script setup>
import { useEventBus } from "@vueuse/core";
import { onMounted, onUnmounted, ref } from "vue";

import { usePanels } from "@/composables/usePanels";

import CenterFrame from "./CenterFrame.vue";
import ActivityBar from "@/components/ActivityBar/ActivityBar.vue";
import HistoryIndex from "@/components/History/Index.vue";
import FlexPanel from "@/components/Panels/FlexPanel.vue";
import ToolPanel from "@/components/Panels/ToolPanel.vue";
import DragAndDropModal from "@/components/Upload/DragAndDropModal.vue";

const showCenter = ref(false);
const { showActivityBar, showToolbox, showPanels } = usePanels();

const { on, off } = useEventBus("router-push");

// methods
function hideCenter() {
    showCenter.value = false;
}

function onLoad() {
    showCenter.value = true;
}

// life cycle
onMounted(() => {
    // Using a custom event here which, in contrast to watching $route,
    // always fires when a route is pushed instead of validating it first.
    on(hideCenter);
});

onUnmounted(() => {
    off(hideCenter);
});
</script>

<template>
    <div id="columns" class="d-flex">
        <ActivityBar v-if="showActivityBar" />
        <FlexPanel v-if="showToolbox" side="left">
            <ToolPanel />
        </FlexPanel>
        <div id="center" class="overflow-auto p-3 w-100">
            <CenterFrame v-show="showCenter" id="galaxy_main" @load="onLoad" />
            <router-view v-show="!showCenter" :key="$route.fullPath" class="h-100" />
        </div>
        <FlexPanel v-if="showPanels" side="right">
            <HistoryIndex />
        </FlexPanel>
        <DragAndDropModal />
    </div>
</template>
