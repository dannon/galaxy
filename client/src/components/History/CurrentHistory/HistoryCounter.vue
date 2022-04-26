<template>
    <div v-if="history.size" class="history-size my-1 d-flex justify-content-between">
        <b-button
            title="Access Dashboard"
            variant="link"
            size="sm"
            class="rounded-0 text-decoration-none"
            @click="onDashboard">
            <font-awesome-icon icon="database" />
            <span>{{ history.size | niceFileSize }}</span>
        </b-button>
        <b-button-group>
            <b-button
                title="Show active"
                variant="link"
                size="sm"
                class="rounded-0 text-decoration-none"
                @click="setFilter('')">
                <font-awesome-icon icon="map-marker" />
                <span>{{ history.contents_active.active }}</span>
            </b-button>
            <b-button
                v-if="history.contents_active.deleted"
                title="Show deleted"
                variant="link"
                size="sm"
                class="rounded-0 text-decoration-none"
                @click="setFilter('deleted=true')">
                <font-awesome-icon icon="trash" />
                <span>{{ history.contents_active.deleted }}</span>
            </b-button>
            <b-button
                v-if="history.contents_active.hidden"
                title="Show hidden"
                variant="link"
                size="sm"
                class="rounded-0 text-decoration-none"
                @click="setFilter('visible=false')">
                <font-awesome-icon icon="eye-slash" />
                <span>{{ history.contents_active.hidden }}</span>
            </b-button>
        </b-button-group>
    </div>
</template>

<script>
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { backboneRoute } from "components/plugins/legacyNavigation";
import prettyBytes from "pretty-bytes";

import { library } from "@fortawesome/fontawesome-svg-core";
import { faDatabase, faMapMarker, faTrash, faEyeSlash } from "@fortawesome/free-solid-svg-icons";
library.add(faDatabase, faMapMarker, faEyeSlash, faTrash);

export default {
    filters: {
        niceFileSize(rawSize = 0) {
            return prettyBytes(rawSize);
        },
    },
    props: {
        history: { type: Object, required: true },
    },
    components: {
        FontAwesomeIcon,
    },
    methods: {
        onDashboard() {
            backboneRoute("/storage");
        },
        setFilter(newFilterText) {
            this.$emit("update:filter-text", newFilterText);
        },
    },
};
</script>
