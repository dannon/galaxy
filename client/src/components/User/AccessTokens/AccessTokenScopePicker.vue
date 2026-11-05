<script setup lang="ts">
/**
 * Picks the scope set for a new access token: a preset, or custom scopes typed by hand.
 * Standalone so other flows (the device-flow approval page) can mount the same picker.
 */
import { computed } from "vue";

import { ACCESS_TOKEN_PRESETS } from "@/api/accessTokens";

import GFormInput from "@/components/BaseComponents/Form/GFormInput.vue";
import GFormLabel from "@/components/BaseComponents/Form/GFormLabel.vue";
import GSelect from "@/components/BaseComponents/GSelect.vue";

const CUSTOM = "custom";

const props = withDefaults(
    defineProps<{
        /** The chosen preset, or "custom" */
        preset?: string;
        /** Space-separated scopes; only edited directly when the preset is "custom" */
        scopes?: string;
        /** Which presets to offer; defaults to all of them */
        presets?: string[];
    }>(),
    { preset: "agent", scopes: "", presets: undefined },
);

const emit = defineEmits<{
    (e: "update:preset", value: string): void;
    (e: "update:scopes", value: string): void;
}>();

const offered = computed(() => ACCESS_TOKEN_PRESETS.filter((p) => !props.presets || props.presets.includes(p.value)));
const options = computed(() => [
    ...offered.value.map((p) => ({ value: p.value, text: p.text })),
    { value: CUSTOM, text: "Custom scopes" },
]);
const current = computed(() => offered.value.find((p) => p.value === props.preset));
const isCustom = computed(() => props.preset === CUSTOM);
const effectiveScopes = computed(() => (isCustom.value ? props.scopes : (current.value?.scopes ?? "")));
const scopeList = computed(() => effectiveScopes.value.split(/\s+/).filter(Boolean));

function onPreset(value: string | null) {
    const preset = value ?? "agent";
    emit("update:preset", preset);
    const chosen = offered.value.find((p) => p.value === preset);
    if (chosen) {
        emit("update:scopes", chosen.scopes);
    }
}
</script>

<template>
    <div class="access-token-scope-picker">
        <GFormLabel title="Access" description="What the token is allowed to do.">
            <GSelect
                id="access-token-preset"
                :model-value="props.preset"
                :options="options"
                @update:model-value="onPreset" />
        </GFormLabel>
        <p v-if="current" class="text-muted small mb-2">{{ current.description }}</p>
        <GFormLabel
            v-if="isCustom"
            title="Scopes"
            description="Space-separated resource:action scopes, e.g. histories:read tools:run.">
            <GFormInput
                id="access-token-scopes"
                :model-value="props.scopes"
                placeholder="histories:read tools:run"
                @update:model-value="(v: string | null) => emit('update:scopes', v ?? '')" />
        </GFormLabel>
        <ul v-else class="list-inline mb-0 small" data-description="scope list">
            <li v-for="scope in scopeList" :key="scope" class="list-inline-item">
                <code>{{ scope }}</code>
            </li>
        </ul>
    </div>
</template>
