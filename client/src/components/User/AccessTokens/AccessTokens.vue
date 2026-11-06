<script setup lang="ts">
import { faBan, faPlus } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { computed, onMounted, ref } from "vue";

import {
    type AccessToken,
    type AccessTokenConstraints,
    type AccessTokenCreatePayload,
    createAccessToken,
    type CreatedAccessToken,
    fetchAccessTokens,
    revokeAccessToken,
} from "@/api/accessTokens";
import { getGalaxyInstance } from "@/app";
import { useConfirmDialog } from "@/composables/confirmDialog";
import { errorMessageAsString } from "@/utils/simple-error";

import AccessTokenScopePicker from "./AccessTokenScopePicker.vue";
import GFormInput from "@/components/BaseComponents/Form/GFormInput.vue";
import GFormLabel from "@/components/BaseComponents/Form/GFormLabel.vue";
import GAlert from "@/components/BaseComponents/GAlert.vue";
import GButton from "@/components/BaseComponents/GButton.vue";
import GCheckbox from "@/components/BaseComponents/GCheckbox.vue";
import GSelect from "@/components/BaseComponents/GSelect.vue";
import BreadcrumbHeading from "@/components/Common/BreadcrumbHeading.vue";
import GTable from "@/components/Common/GTable.vue";
import CopyToClipboard from "@/components/CopyToClipboard.vue";
import LoadingSpan from "@/components/LoadingSpan.vue";
import UtcDate from "@/components/UtcDate.vue";

const DAY = 24 * 3600;
const LIFETIMES = [
    { value: DAY, text: "1 day" },
    { value: 7 * DAY, text: "7 days" },
    { value: 30 * DAY, text: "30 days" },
    { value: 90 * DAY, text: "90 days" },
];

const currentUserId: string = getGalaxyInstance().user.id;
const { confirm } = useConfirmDialog();

const tokens = ref<AccessToken[]>([]);
const loading = ref(false);
const errorMessage = ref<string | null>(null);
const created = ref<CreatedAccessToken | null>(null);

const name = ref("");
const preset = ref("agent");
const scopes = ref("");
const expiresIn = ref(30 * DAY);
const mcpOnly = ref(false);
const ownHistoriesOnly = ref(false);
const toolIds = ref("");
const creating = ref(false);

const breadcrumbItems = [{ title: "User Preferences", to: "/user" }, { title: "Access Tokens" }];
const fields = [
    { key: "name", label: "Name" },
    { key: "token_hint", label: "Token" },
    { key: "scopes", label: "Scopes" },
    { key: "constraints", label: "Limits" },
    { key: "expires_at", label: "Expires" },
    { key: "last_used_at", label: "Last used" },
    { key: "action", label: "" },
];

const constraints = computed<AccessTokenConstraints | null>(() => {
    const result: AccessTokenConstraints = {};
    if (mcpOnly.value) {
        result.surfaces = ["mcp"];
    }
    if (ownHistoriesOnly.value) {
        result.histories = { created_by_token: true };
    }
    const ids = toolIds.value.split(/[\s,]+/).filter(Boolean);
    if (ids.length) {
        result.tools = { ids };
    }
    return Object.keys(result).length ? result : null;
});
const canCreate = computed(() => name.value.trim().length > 0 && !creating.value);

async function load() {
    loading.value = true;
    try {
        tokens.value = await fetchAccessTokens(currentUserId);
        errorMessage.value = null;
    } catch (e) {
        errorMessage.value = errorMessageAsString(e);
    } finally {
        loading.value = false;
    }
}

async function create() {
    if (!canCreate.value) {
        // Enter in a text field submits the form even while the button is disabled
        return;
    }
    creating.value = true;
    // a new request replaces the old secret; it must never read as the new token's
    created.value = null;
    try {
        const payload: AccessTokenCreatePayload = {
            name: name.value.trim(),
            expires_in: expiresIn.value,
            constraints: constraints.value,
            ...(preset.value === "custom" ? { scopes: scopes.value } : { preset: preset.value }),
        };
        created.value = await createAccessToken(currentUserId, payload);
        name.value = "";
        errorMessage.value = null;
        await load();
    } catch (e) {
        errorMessage.value = errorMessageAsString(e);
    } finally {
        creating.value = false;
    }
}

async function revoke(token: AccessToken) {
    const confirmed = await confirm(`Revoke "${token.name}"? Anything still using it will stop working.`, {
        title: "Revoke access token",
        okText: "Revoke",
        okIcon: faBan,
        okColor: "red",
    });
    if (!confirmed) {
        return;
    }
    try {
        await revokeAccessToken(currentUserId, token.id);
        if (created.value?.id === token.id) {
            created.value = null;
        }
        await load();
    } catch (e) {
        errorMessage.value = errorMessageAsString(e);
    }
}

function describeConstraints(value: AccessToken["constraints"]): string {
    if (!value) {
        return "none";
    }
    const parts: string[] = [];
    if (value.surfaces) {
        parts.push(`${value.surfaces.join(", ")} only`);
    }
    if (value.histories?.created_by_token) {
        parts.push("own histories");
    }
    if (value.histories?.ids?.length) {
        parts.push(`${value.histories.ids.length} histories`);
    }
    if (value.tools?.ids?.length) {
        parts.push(`tools: ${value.tools.ids.join(", ")}`);
    }
    if (value.workflows?.ids?.length) {
        parts.push(`${value.workflows.ids.length} workflows`);
    }
    return parts.join("; ") || "none";
}

onMounted(load);
</script>

<template>
    <section class="access-tokens">
        <BreadcrumbHeading :items="breadcrumbItems" />

        <p v-localize>
            Access tokens let a client, script or AI agent use the Galaxy API with only the access you give it. Each
            token expires and can be revoked on its own without affecting your other tokens or your API key.
        </p>

        <GAlert :show="!!errorMessage" dismissible variant="warning" @dismissed="errorMessage = null">
            {{ errorMessage }}
        </GAlert>

        <GAlert
            v-if="created"
            show
            variant="success"
            dismissible
            data-description="created token"
            @dismissed="created = null">
            <p class="mb-1">
                <strong v-localize>Copy your new token now. It will not be shown again.</strong>
            </p>
            <div class="d-flex align-items-center">
                <code class="mr-2 created-token-secret">{{ created.token }}</code>
                <CopyToClipboard :text="created.token" message="Token copied to your clipboard" title="Copy token" />
            </div>
        </GAlert>

        <form class="access-token-form mb-4" @submit.prevent="create">
            <h2 v-localize class="h-md">Create a token</h2>
            <GFormLabel
                title="Name"
                description="Something that says where it will be used, like 'Claude Code on my laptop'.">
                <GFormInput id="access-token-name" v-model="name" placeholder="Name" />
            </GFormLabel>
            <AccessTokenScopePicker v-model:preset="preset" v-model:scopes="scopes" />
            <GFormLabel title="Expires in">
                <GSelect id="access-token-lifetime" v-model="expiresIn" :options="LIFETIMES" />
            </GFormLabel>
            <GCheckbox id="access-token-mcp-only" v-model="mcpOnly" class="mb-1">
                <span v-localize>Only usable with the MCP server (not the REST API)</span>
            </GCheckbox>
            <GCheckbox id="access-token-own-histories" v-model="ownHistoriesOnly" class="mb-2">
                <span v-localize>Only histories this token creates</span>
            </GCheckbox>
            <GFormLabel
                title="Tools"
                description="Optional. Only these tool ids may be run or built; leave empty for any tool.">
                <GFormInput
                    id="access-token-tools"
                    v-model="toolIds"
                    placeholder="tool ids, comma or space separated" />
            </GFormLabel>
            <GButton type="submit" color="blue" :disabled="!canCreate" data-description="create token">
                <FontAwesomeIcon :icon="faPlus" />
                <span v-localize>Create token</span>
            </GButton>
        </form>

        <h2 v-localize class="h-md">Your tokens</h2>
        <GAlert v-if="loading" show variant="info">
            <LoadingSpan message="Loading access tokens" />
        </GAlert>
        <GTable
            v-else
            compact
            show-empty
            :items="tokens"
            :fields="fields"
            :empty-state="{ message: 'No access tokens yet.' }">
            <template v-slot:cell(token_hint)="row">
                <code>gxy_{{ row.item.token_hint }}&hellip;</code>
            </template>
            <template v-slot:cell(scopes)="row">
                <span class="small">{{ row.item.scopes.join(" ") }}</span>
            </template>
            <template v-slot:cell(constraints)="row">
                <span class="small">{{ describeConstraints(row.item.constraints) }}</span>
            </template>
            <template v-slot:cell(expires_at)="row">
                <UtcDate v-if="row.item.expires_at" :date="row.item.expires_at" mode="elapsed" />
                <span v-else v-localize>never</span>
            </template>
            <template v-slot:cell(last_used_at)="row">
                <UtcDate v-if="row.item.last_used_at" :date="row.item.last_used_at" mode="elapsed" />
                <span v-else v-localize>never</span>
            </template>
            <template v-slot:cell(action)="row">
                <GButton
                    size="small"
                    color="red"
                    outline
                    title="Revoke token"
                    :aria-label="`Revoke ${row.item.name}`"
                    data-description="revoke token"
                    @click="revoke(row.item)">
                    <FontAwesomeIcon :icon="faBan" />
                    <span v-localize>Revoke</span>
                </GButton>
            </template>
        </GTable>
    </section>
</template>

<style scoped>
.access-token-form {
    max-width: 40rem;
}
.created-token-secret {
    word-break: break-all;
}
</style>
