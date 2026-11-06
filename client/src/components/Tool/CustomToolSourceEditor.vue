<script setup lang="ts">
/**
 * Monaco editor for a custom tool's YAML source plus the save that creates a new revision.
 * Embeddable: the full-page tool editor wraps it with its own chrome, and the workflow editor's
 * node inspector mounts it next to the graph.
 */
import "./monacoEnvironment";

import { faSave } from "@fortawesome/free-regular-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { useMonaco, VueMonacoEditor } from "@guolao/vue-monaco-editor";
import { nextTick, onUnmounted, ref, watch } from "vue";
import { parse, stringify } from "yaml";

import {
    type DynamicUnprivilegedToolCreatePayload,
    GalaxyApi,
    type MessageException,
    type UnprivilegedToolResponse,
} from "@/api";
import { useUnprivilegedToolStore } from "@/stores/unprivilegedToolStore";

import { NEW_TOOL_YAML } from "./customToolEditorDefaults";
import { setupMonaco } from "./YamlJs";

import GAlert from "@/components/BaseComponents/GAlert.vue";
import GButton from "@/components/BaseComponents/GButton.vue";

const props = withDefaults(
    defineProps<{
        /** Revision to load into the editor; a new tool otherwise */
        toolUuid?: string;
        /** Render the save (and cancel) buttons below the editor */
        toolbar?: boolean;
    }>(),
    { toolUuid: undefined, toolbar: false },
);

const emit = defineEmits<{
    (e: "saved", tool: UnprivilegedToolResponse): void;
    (e: "cancel"): void;
}>();

const { unload, monacoRef } = useMonaco();
const unprivilegedToolStore = useUnprivilegedToolStore();

const disposeConfig = ref<() => void>();
watch(
    monacoRef,
    () => {
        const monaco = monacoRef.value;
        if (monaco) {
            nextTick().then(() => {
                setupMonaco(monaco).then((dispose) => {
                    disposeConfig.value = dispose;
                });
            });
        }
    },
    { immediate: true },
);

onUnmounted(() => {
    disposeConfig.value?.();
    unload();
});

const errorMsg = ref<MessageException>();
const saving = ref(false);
const yamlRepresentation = ref<string>(NEW_TOOL_YAML);

if (props.toolUuid) {
    GalaxyApi()
        .GET("/api/unprivileged_tools/{uuid}", {
            params: {
                path: {
                    uuid: props.toolUuid,
                },
            },
        })
        .then(({ data, error }) => {
            if (error) {
                errorMsg.value = error;
            } else {
                yamlRepresentation.value = stringify(data.representation, {
                    blockQuote: "literal",
                });
            }
        });
}

/** Saves the source as a new tool revision and reports it through `saved`. */
async function save() {
    if (saving.value) {
        return;
    }
    if (!yamlRepresentation.value.trim()) {
        errorMsg.value = { err_code: -1, err_msg: "The tool source is empty; nothing to save." };
        return;
    }
    let representation;
    try {
        representation = parse(yamlRepresentation.value);
    } catch (error) {
        errorMsg.value = { err_code: -1, err_msg: `Tool source is not valid YAML: ${error}` };
        return;
    }
    const payload: DynamicUnprivilegedToolCreatePayload = {
        active: true,
        hidden: false,
        representation,
        src: "representation",
    };
    saving.value = true;
    try {
        const { data, error } = await GalaxyApi().POST("/api/unprivileged_tools", { body: payload });
        if (error) {
            errorMsg.value = error;
        } else {
            errorMsg.value = undefined;
            unprivilegedToolStore.load(true);
            emit("saved", data);
        }
    } finally {
        saving.value = false;
    }
}

/** Replaces the source, for hosts that import or generate it. */
function setSource(yaml: string) {
    yamlRepresentation.value = yaml;
}

defineExpose({ save, saving, setSource });
</script>

<template>
    <div class="custom-tool-source-editor">
        <GAlert v-if="errorMsg" variant="danger" show dismissible>
            {{ errorMsg.err_msg }}
        </GAlert>
        <VueMonacoEditor
            v-model="yamlRepresentation"
            class="custom-tool-source-editor-monaco"
            language="yaml-with-js"
            default-path="tool.yml"
            :options="{
                quickSuggestions: {
                    other: true,
                    comments: false,
                    strings: true,
                },
            }">
        </VueMonacoEditor>
        <div v-if="props.toolbar" class="custom-tool-source-editor-toolbar">
            <GButton size="small" data-description="cancel custom tool edit" :disabled="saving" @click="emit('cancel')">
                Cancel
            </GButton>
            <GButton
                color="blue"
                size="small"
                data-description="save custom tool revision"
                :disabled="saving"
                @click="save">
                <FontAwesomeIcon :icon="faSave" class="mr-1" />
                Save as new revision
            </GButton>
        </div>
    </div>
</template>

<style scoped>
.custom-tool-source-editor {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
}

.custom-tool-source-editor-monaco {
    flex: 1;
    min-height: 0;
}

.custom-tool-source-editor-toolbar {
    display: flex;
    justify-content: flex-end;
    gap: var(--spacing-2);
    padding-top: var(--spacing-2);
}
</style>
