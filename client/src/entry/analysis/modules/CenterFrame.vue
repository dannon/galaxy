<template>
    <iframe
        :id="id"
        :name="id"
        :src="srcWithRoot"
        class="center-frame"
        frameborder="0"
        title="galaxy frame"
        width="100%"
        height="100%"
        @load="onLoad" />
</template>

<script setup lang="ts">
import { computed } from "vue";

import { withPrefix } from "@/utils/redirect";

const props = withDefaults(
    defineProps<{
        id?: string;
        src?: string;
    }>(),
    {
        id: "frame",
        src: "",
    }
);

// Computed property
const srcWithRoot = computed(() => withPrefix(props.src));

// Event handler
const onLoad = (ev: Event) => {
    const iframe = ev.currentTarget as HTMLIFrameElement;
    const location = iframe.contentWindow?.location;
    try {
        if (location && location.host && location.pathname != "/") {
            emit("load");
        }
    } catch (err) {
        console.warn("CenterFrame - onLoad location access forbidden.", ev, location);
    }
};

// Emit setup
const emit = defineEmits(["load"]);
</script>
