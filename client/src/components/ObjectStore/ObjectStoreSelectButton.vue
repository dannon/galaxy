<script setup lang="ts">
import type { ConcreteObjectStoreModel } from "@/api";
import { variantToColor } from "@/components/BaseComponents/variantToColor";

import GButton from "@/components/BaseComponents/GButton.vue";
import ObjectStoreBadges from "@/components/ObjectStore/ObjectStoreBadges.vue";
import ProvidedQuotaSourceUsageBar from "@/components/User/DiskUsage/Quota/ProvidedQuotaSourceUsageBar.vue";

interface ObjectStoreSelectButtonProps {
    objectStore: ConcreteObjectStoreModel;
    variant: string;
    idPrefix: string;
}

defineProps<ObjectStoreSelectButtonProps>();

const emit = defineEmits<{
    (e: "click", value: string | null): void;
}>();
</script>

<template>
    <GButton
        :id="`${idPrefix}-object-store-button-${objectStore.object_store_id}`"
        v-bind="variantToColor(variant)"
        :data-object-store-id="objectStore.object_store_id"
        @click="emit('click', objectStore.object_store_id ?? null)"
        >{{ objectStore.name }}
        <ObjectStoreBadges :badges="objectStore.badges" size="lg" :more-on-hover="false" />
        <ProvidedQuotaSourceUsageBar :object-store="objectStore" :compact="true"> </ProvidedQuotaSourceUsageBar>
    </GButton>
</template>
