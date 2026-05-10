<script setup>
import { faEdit, faFile, faFolderOpen, faLock } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { BAlert } from "bootstrap-vue";
import { computed, ref } from "vue";

import { getGalaxyInstance } from "@/app";
import { variantToColor } from "@/components/BaseComponents/variantToColor";
import { buildCollectionFromRules } from "@/components/Collections/common/buildCollectionModal";
import { getRemoteEntries, getRemoteEntriesAt } from "@/components/Upload/utils";
import { filesDialog } from "@/utils/dataModals";
import { urlData } from "@/utils/url";

import { RULES_TYPES } from "./utils.js";

import UploadSelect from "./UploadSelect.vue";
import GButton from "@/components/BaseComponents/GButton.vue";

const props = defineProps({
    hasCallback: {
        type: Boolean,
        default: false,
    },
    fileSourcesConfigured: {
        type: Boolean,
        required: true,
    },
    ftpUploadSite: {
        type: String,
        default: null,
    },
    historyId: {
        type: String,
        required: true,
    },
});

const emit = defineEmits(["dismiss"]);

const dataType = ref("datasets");
const errorMessage = ref(null);
const ftpFiles = ref([]);
const selectedDatasetId = ref(null);
const selectionType = ref("raw");
const sourceContent = ref(null);
const uris = ref([]);

const isDisabled = computed(() => selectionType.value !== "raw");

function eventBuild() {
    const entry = {
        dataType: dataType.value,
        selectionType: selectionType.value,
    };
    if (entry.selectionType == "ftp") {
        entry.elements = ftpFiles.value;
        entry.ftpUploadSite = props.ftpUploadSite;
    } else if (entry.selectionType === "raw") {
        entry.content = sourceContent.value;
    } else if (entry.selectionType == "remote_files") {
        entry.elements = uris.value;
    }
    buildCollectionFromRules(entry, null, true);
    emit("dismiss");
}

function eventReset() {
    selectedDatasetId.value = null;
    selectionType.value = "raw";
    sourceContent.value = null;
}

function inputDialog() {
    const Galaxy = getGalaxyInstance();
    Galaxy.data.dialog(
        (response) => {
            selectedDatasetId.value = response.id;
            urlData({ url: `/api/histories/${props.historyId}/contents/${selectedDatasetId.value}/display` })
                .then((newSourceContent) => {
                    selectionType.value = "raw";
                    sourceContent.value = newSourceContent;
                })
                .catch((error) => {
                    errorMessage.value = error;
                });
        },
        {
            multiple: false,
            library: false,
            format: null,
            allowUpload: false,
        },
    );
}

function inputFtp() {
    getRemoteEntries((ftp_files) => {
        selectionType.value = "ftp";
        sourceContent.value = ftp_files.map((file) => file["path"]).join("\n");
        ftpFiles.value = ftp_files;
    });
}

function inputPaste() {
    selectionType.value = "raw";
    selectedDatasetId.value = null;
    sourceContent.value = null;
}

function inputRemote() {
    function handleRemoteFilesUri(record) {
        getRemoteEntriesAt(record.url).then((files) => {
            files = files.filter((file) => file["class"] == "File");
            selectionType.value = "remote_files";
            sourceContent.value = files.map((file) => file["uri"]).join("\n");
            uris.value = files;
        });
    }
    filesDialog(handleRemoteFilesUri, { mode: "directory" });
}
</script>

<template>
    <div class="upload-wrapper d-flex flex-column">
        <BAlert v-if="errorMessage" variant="danger" show>{{ errorMessage }}</BAlert>
        <div v-localize class="upload-header">Insert tabular source data to extract collection files and metadata.</div>
        <textarea
            v-model="sourceContent"
            class="upload-box upload-rule-source-content"
            :placeholder="localize('Insert tabular source data here.')"
            :disabled="isDisabled" />
        <FontAwesomeIcon v-if="isDisabled" class="upload-text-lock" :icon="faLock" />
        <div class="upload-footer text-center">
            <span v-localize class="upload-footer-title">Upload type:</span>
            <UploadSelect v-model="dataType" class="rule-data-type" :options="RULES_TYPES" :searchable="false" />
        </div>
        <div class="upload-buttons d-flex justify-content-end">
            <GButton @click="inputPaste">
                <FontAwesomeIcon :icon="faEdit" />
                <span v-localize>Paste data</span>
            </GButton>
            <GButton data-description="rules dataset dialog" @click="inputDialog">
                <FontAwesomeIcon :icon="faFile" />
                <span v-localize>Choose dataset</span>
            </GButton>
            <GButton v-if="ftpUploadSite" @click="inputFtp">
                <FontAwesomeIcon :icon="faFolderOpen" />
                <span v-localize>Import FTP files</span>
            </GButton>
            <GButton @click="inputRemote">
                <FontAwesomeIcon :icon="faFolderOpen" />
                <span v-localize>Choose from repository</span>
            </GButton>
            <GButton
                id="btn-build"
                :disabled="!sourceContent"
                title="Build"
                v-bind="variantToColor(sourceContent ? 'primary' : '')"
                @click="eventBuild">
                <span v-localize>Build</span>
            </GButton>
            <GButton id="btn-reset" title="Reset" :disabled="!sourceContent" @click="eventReset">
                <span v-localize>Reset</span>
            </GButton>
            <GButton id="btn-close" title="Close" @click="$emit('dismiss')">
                <span v-localize>Close</span>
            </GButton>
        </div>
    </div>
</template>

<style scoped>
.upload-rule-source-content {
    resize: none;
}
.upload-text-lock {
    bottom: 22%;
    font-size: 1.275rem;
    opacity: 0.2;
    right: 3%;
    position: absolute;
}
</style>
