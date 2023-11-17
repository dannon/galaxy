<script setup>
import { useElementBounding } from "@vueuse/core";
import { computed, ref, watch } from "vue";
import VirtualList from "vue-virtual-scroll-list";

import LoadingSpan from "@/components/LoadingSpan.vue";

const props = defineProps({
    dataKey: { type: String, default: "id" },
    offset: { type: Number, default: 0 },
    loading: { type: Boolean, default: false },
    items: { type: Array, default: () => [] },
    queryKey: { type: String, default: null },
});

const emits = defineEmits(["scroll"]);

const listing = ref(null);
const layout = ref(null);

const { height } = useElementBounding(layout);
const estimatedItemHeight = 40;

const estimatedItemCount = computed(() => {
    const baseCount = Math.ceil(height.value / estimatedItemHeight);
    return baseCount + 20;
});

let previousStart = undefined;

watch(
    () => props.queryKey,
    () => {
        listing.value?.scrollToOffset(0);
    }
);

const onScroll = () => {
    const rangeStart = listing.value.range.start;
    if (previousStart !== rangeStart) {
        previousStart = rangeStart;
        emits("scroll", rangeStart);
    }
};

const getOffset = () => listing.value?.getOffset() || 0;
</script>
<template>
    <div ref="layout" class="listing-layout">
        <VirtualList
            ref="listing"
            class="listing"
            role="list"
            :data-key="dataKey"
            :offset="offset"
            :data-sources="items"
            :data-component="{}"
            :estimate-size="estimatedItemHeight"
            :keeps="estimatedItemCount"
            @scroll="onScroll">
            <template v-slot:item="{ item }">
                <slot name="item" :item="item" :current-offset="getOffset" />
            </template>
            <template v-slot:footer>
                <LoadingSpan v-if="loading" class="m-2" message="Loading" />
            </template>
        </VirtualList>
    </div>
</template>

<style scoped lang="scss">
@import "scss/mixins.scss";
.listing-layout {
    .listing {
        @include absfill();
        scroll-behavior: smooth;
        overflow-y: scroll;
        overflow-x: hidden;
    }
}
</style>
