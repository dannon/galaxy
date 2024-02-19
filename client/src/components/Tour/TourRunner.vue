<template>
    <CenterFrame src="/welcome" />
</template>

<script>
import { runTour } from "./runTour";

import CenterFrame from "@/entry/analysis/modules/CenterFrame.vue";

export default {
    components: {
        CenterFrame,
    },
    props: {
        tourId: {
            type: String,
            required: true,
        },
    },
    mounted() {
        runTour(this.tourId);
    },
};
</script>
