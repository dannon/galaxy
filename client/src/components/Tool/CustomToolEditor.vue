<script setup lang="ts">
import { faArrowAltCircleUp, faLightbulb, faSave } from "@fortawesome/free-regular-svg-icons";
import { faBookOpen, faEraser } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { nextTick, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";

import { GalaxyApi, type MessageException, type UnprivilegedToolResponse } from "@/api";

import { linkedAuthoringHelpSection } from "./authoringHelp";
import { CLEAR_TOOL_YAML } from "./customToolEditorDefaults";

import GAlert from "@/components/BaseComponents/GAlert.vue";
import GButton from "@/components/BaseComponents/GButton.vue";
import Heading from "@/components/Common/Heading.vue";
import AuthoringHelpPanel from "@/components/Tool/AuthoringHelpPanel.vue";
import CustomToolEditorWorkspace from "@/components/Tool/CustomToolEditorWorkspace.vue";
import CustomToolSourceEditor from "@/components/Tool/CustomToolSourceEditor.vue";

const router = useRouter();

onMounted(() => document.addEventListener("click", openAuthoringHelpLink, { capture: true }));
onUnmounted(() => {
    document.removeEventListener("click", openAuthoringHelpLink, { capture: true });
});

interface ExistingTool {
    toolUuid?: string;
}
const props = defineProps<ExistingTool>();
const errorMsg = ref<MessageException>();
const sourceEditor = ref<InstanceType<typeof CustomToolSourceEditor>>();

function saveTool() {
    sourceEditor.value?.save();
}

function onSaved(tool: UnprivilegedToolResponse) {
    router.push(`/tools/editor/${tool.uuid}`);
}

async function importFromUrl() {
    const url = prompt("Enter the URL to import YAML from:");
    if (!url) {
        return;
    }

    try {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error("Failed to fetch YAML");
        }

        const yaml = await response.text();
        sourceEditor.value?.setSource(yaml);
    } catch (error) {
        errorMsg.value = { err_code: -1, err_msg: `Couldn't import YAML from URL: ${error}` };
    }
}

function clearTool() {
    if (window.confirm("Replace the current tool definition with the minimal skeleton?")) {
        sourceEditor.value?.setSource(CLEAR_TOOL_YAML);
    }
}

const generating = ref(false);
const showDocumentation = ref(false);
const authoringHelpPanel = ref<InstanceType<typeof AuthoringHelpPanel>>();

async function openAuthoringHelpLink(event: MouseEvent) {
    if (event.defaultPrevented) {
        return;
    }
    const target = event.target;
    if (!(target instanceof Element)) {
        return;
    }
    const link = target.closest<HTMLAnchorElement>("a[href]");
    const href = link?.dataset.href ?? link?.getAttribute("href") ?? "";
    const sectionId = linkedAuthoringHelpSection(href);
    if (!sectionId) {
        return;
    }

    event.preventDefault();
    event.stopPropagation();
    target
        .closest<HTMLElement>(".monaco-hover")
        ?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", keyCode: 27, bubbles: true }));
    showDocumentation.value = true;
    await nextTick();
    await authoringHelpPanel.value?.openSection(sectionId);
}

async function generateViaLLM() {
    const userPrompt = prompt("Describe the tool you would like to build");
    if (!userPrompt) {
        return;
    }

    generating.value = true;
    errorMsg.value = undefined;

    try {
        const response = await GalaxyApi().POST("/api/ai/agents/custom-tool", {
            body: { query: userPrompt },
        });

        if (response.error) {
            errorMsg.value = { err_code: response.error.err_code, err_msg: response.error.err_msg };
            return;
        }

        // Check if the response contains tool YAML in metadata
        const metadata = response.data?.metadata as { tool_yaml?: string; error?: string } | undefined;
        const toolYaml = metadata?.tool_yaml;
        if (toolYaml) {
            sourceEditor.value?.setSource(toolYaml);
        } else if (metadata?.error === "model_capability") {
            // Model doesn't support structured output
            errorMsg.value = {
                err_code: -1,
                err_msg:
                    "The configured AI model doesn't support tool generation. Please contact your administrator to configure a compatible model.",
            };
        } else {
            const content = response.data?.content as string | undefined;
            errorMsg.value = {
                err_code: -1,
                err_msg: content ?? "Failed to generate tool definition",
            };
        }
    } catch (error) {
        errorMsg.value = { err_code: -1, err_msg: `Couldn't generate YAML: ${error}` };
    } finally {
        generating.value = false;
    }
}
</script>

<template>
    <div class="custom-tool-editor">
        <GAlert v-if="errorMsg" variant="danger" show dismissible>
            {{ errorMsg.err_msg }}
        </GAlert>
        <div class="d-flex flex-gapx-1">
            <Heading h1 separator inline size="lg" class="flex-grow-1 mb-2">Tool Editor</Heading>
            <GButton
                outline
                icon-only
                tooltip
                size="large"
                title="Generate via AI"
                disabled-title="Generating via AI"
                aria-label="Generate via AI"
                data-description="Generate via AI"
                :disabled="generating"
                @click="generateViaLLM">
                <FontAwesomeIcon :icon="faLightbulb" />
            </GButton>
            <GButton
                outline
                icon-only
                tooltip
                size="large"
                title="Import from URL"
                aria-label="Import from URL"
                data-description="Import from a URL"
                @click="importFromUrl">
                <FontAwesomeIcon :icon="faArrowAltCircleUp" />
            </GButton>
            <GButton
                outline
                icon-only
                tooltip
                size="large"
                title="Clear Tool"
                aria-label="Clear Tool"
                data-description="clear custom tool"
                @click="clearTool">
                <FontAwesomeIcon :icon="faEraser" />
            </GButton>
            <GButton
                outline
                icon-only
                tooltip
                size="large"
                title="Documentation"
                aria-label="Documentation"
                aria-controls="custom-tool-documentation-panel"
                :aria-expanded="showDocumentation ? 'true' : 'false'"
                data-description="toggle tool documentation"
                :pressed="showDocumentation"
                @click="showDocumentation = !showDocumentation">
                <FontAwesomeIcon :icon="faBookOpen" />
            </GButton>
            <GButton
                color="blue"
                icon-only
                tooltip
                size="large"
                title="Save Custom Tool"
                aria-label="Save Custom Tool"
                data-description="save custom tool"
                :disabled="sourceEditor?.saving || sourceEditor?.loading"
                @click="saveTool">
                <FontAwesomeIcon :icon="faSave" />
            </GButton>
        </div>
        <CustomToolEditorWorkspace :documentation-visible="showDocumentation">
            <template v-slot:editor>
                <CustomToolSourceEditor
                    :key="props.toolUuid"
                    ref="sourceEditor"
                    :tool-uuid="props.toolUuid"
                    @saved="onSaved" />
            </template>
            <template v-slot:documentation>
                <AuthoringHelpPanel ref="authoringHelpPanel" class="p-3" />
            </template>
        </CustomToolEditorWorkspace>
    </div>
</template>

<style scoped>
.custom-tool-editor {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
}
</style>
