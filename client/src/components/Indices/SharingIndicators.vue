<script setup lang="ts">
import { library } from "@fortawesome/fontawesome-svg-core";
import { faGlobe, faLink, faShareAlt, faUsers } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { BButton } from "bootstrap-vue";
import { computed } from "vue";

import { localize } from "@/utils/localization";

library.add(faGlobe, faShareAlt, faLink, faUsers);

interface SharingIndicatorsProps {
    object: {
        deleted?: boolean;
        importable?: boolean;
        published?: boolean;
        purged?: boolean;
        shared?: boolean;
    };
}
const props = defineProps<SharingIndicatorsProps>();

const localized = computed(() => {
    return {
        findPublished: localize("Find all published items"),
        findImportable: localize("Find all importable items"),
        findShared: localize("Find all items shared with me"),
    };
});
</script>

<template>
    <span v-if="props.object.purged" v-localize> Purged </span>
    <span v-else-if="props.object.deleted" v-localize> Deleted </span>
    <span v-else>
        <BButton
            v-if="props.object.published"
            v-b-tooltip.hover.noninteractive
            class="sharing-indicator-published"
            size="sm"
            variant="link"
            :title="localized.findPublished"
            @click.prevent="$emit('filter', 'published')">
            <FontAwesomeIcon icon="globe" />
        </BButton>
        <BButton
            v-if="props.object.importable"
            v-b-tooltip.hover.noninteractive
            class="sharing-indicator-importable"
            size="sm"
            variant="link"
            :title="localized.findImportable"
            @click.prevent="$emit('filter', 'importable')">
            <FontAwesomeIcon icon="link" />
        </BButton>
        <BButton
            v-if="props.object.shared"
            v-b-tooltip.hover.noninteractive
            class="sharing-indicator-shared"
            size="sm"
            variant="link"
            :title="localized.findShared"
            @click.prevent="$emit('filter', 'shared_with_me')">
            <FontAwesomeIcon icon="share-alt" />
        </BButton>
    </span>
</template>
