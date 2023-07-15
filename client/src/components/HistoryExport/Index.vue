<template>
    <span class="history-export-component">
        <h1 class="h-lg">Export history archive</h1>
        <span v-if="initializingFileSources">
            <loading-span :message="initializeFileSourcesMessage" />
        </span>
        <span v-else-if="hasWritableFileSources">
            <GCard no-body>
                <GTabs pills card vertical>
                    <GTab title="to a link" title-link-class="tab-export-to-link" active>
                        <GCardText>
                            <ToLink :history-id="historyId" />
                        </GCardText>
                    </GTab>
                    <GTab title="to a remote file" title-link-class="tab-export-to-file">
                        <GCardText>
                            <ToRemoteFile :history-id="historyId" />
                        </GCardText>
                    </GTab>
                </GTabs>
            </GCard>
        </span>
        <span v-else>
            <ToLink :history-id="historyId" />
        </span>
    </span>
</template>

<script>
import { BCard } from "bootstrap-vue";
import exportsMixin from "components/Common/exportsMixin";

import ToLink from "./ToLink.vue";
import ToRemoteFile from "./ToRemoteFile.vue";
import GTab from "@/component-library/GTab.vue";
import GTabs from "@/component-library/GTabs.vue";

export default {
    components: {
        GCardText,
        ToLink,
        ToRemoteFile,
        BCard,
        GTabs,
        GTab,
    },
    mixins: [exportsMixin],
    props: {
        historyId: {
            type: String,
            required: true,
        },
    },
    async mounted() {
        await this.initializeFilesSources();
    },
};
</script>
