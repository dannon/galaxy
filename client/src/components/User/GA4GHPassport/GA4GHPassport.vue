<script setup lang="ts">
import { faPlug, faSync, faTrash } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { computed, onMounted, ref } from "vue";

import { GalaxyApi } from "@/api";
import type { components } from "@/api/schema";
import { useConfirmDialog } from "@/composables/confirmDialog";
import { galaxyTimeToDate } from "@/utils/dates";
import localize from "@/utils/localization";
import { withPrefix } from "@/utils/redirect";
import { errorMessageAsString } from "@/utils/simple-error";

import GAlert from "@/components/BaseComponents/GAlert.vue";
import GButton from "@/components/BaseComponents/GButton.vue";
import BreadcrumbHeading from "@/components/Common/BreadcrumbHeading.vue";
import LoadingSpan from "@/components/LoadingSpan.vue";
import UtcDate from "@/components/UtcDate.vue";

type PassportStatus = components["schemas"]["GA4GHPassportStatus"];

interface Props {
    /** `ga4gh_passport` query parameter set by the connect callback ("connected"). */
    outcome?: string;
    /** `ga4gh_passport_error` query parameter set by the connect callback. */
    error?: string;
}

const props = defineProps<Props>();

const PATH = "/user/ga4gh_passport";
const CONNECT_URL = `/ga4gh_passport/connect?next=${encodeURIComponent(PATH)}`;
// Warn this long before the Passport stops being usable.
const SOON_MS = 10 * 60 * 1000;

const CONNECT_ERRORS: Record<string, string> = {
    access_denied: "You declined to share your Passport with Galaxy.",
    identity_mismatch:
        "Your Galaxy account is linked to a different identity. Sign out of the identity provider and connect with the linked identity.",
    identity_in_use: "That identity is already linked to another Galaxy account.",
    invalid_passport: "The Passport could not be verified.",
    invalid_flow: "The connection attempt expired or was already used. Please try again.",
};

const breadcrumbItems = [{ title: "User Preferences", to: "/user" }, { title: "GA4GH Passport" }];

const { confirm } = useConfirmDialog();

const status = ref<PassportStatus | null>(null);
const loading = ref(true);
const errorMessage = ref<string | null>(null);
const forgetting = ref(false);

const outcomeMessage = props.outcome === "connected" ? localize("Your GA4GH Passport is connected.") : null;
const connectErrorMessage = props.error
    ? `${localize(CONNECT_ERRORS[props.error] ?? "Connecting your Passport failed.")} (${props.error})`
    : null;

const identityName = computed(() => {
    const identity = status.value?.identity;
    return identity ? identity.preferred_username || identity.sub : null;
});
const grants = computed(() => (status.value?.passport?.visas ?? []).flatMap((visa) => visa.grants));
const unrecognizedVisas = computed(
    () => (status.value?.passport?.visas ?? []).filter((visa) => !visa.recognized).length,
);
const usableUntil = computed(() => {
    const passport = status.value?.passport;
    return passport ? galaxyTimeToDate(passport.usable_until) : null;
});
const usableSoonEnds = computed(() => usableUntil.value !== null && usableUntil.value.getTime() - Date.now() < SOON_MS);

async function load() {
    loading.value = true;
    const { data, error } = await GalaxyApi().GET("/api/users/current/ga4gh_passport");
    if (error) {
        errorMessage.value = errorMessageAsString(error);
    } else {
        status.value = data;
    }
    loading.value = false;
}

function connect() {
    window.location.href = withPrefix(CONNECT_URL);
}

async function forget() {
    const confirmed = await confirm(
        localize(
            `Galaxy will delete its copy of your Passport and stop using it for data requests. Your Galaxy account stays linked to ${identityName.value}, and data you already fetched stays available until your grants in Galaxy expire; connect again to get a new Passport.`,
        ),
        { title: localize("Forget your GA4GH Passport?"), okText: localize("Forget"), okColor: "red", okIcon: faTrash },
    );
    if (!confirmed) {
        return;
    }
    forgetting.value = true;
    const { error } = await GalaxyApi().DELETE("/api/users/current/ga4gh_passport");
    forgetting.value = false;
    if (error) {
        errorMessage.value = errorMessageAsString(error);
        return;
    }
    await load();
}

onMounted(async () => {
    if (props.outcome || props.error) {
        // Drop the callback's query parameters so a reload doesn't repeat the message. Not
        // router.replace(): the router view is keyed by the full path, so it would remount the
        // page without them and the message would be lost.
        window.history.replaceState(window.history.state, "", window.location.pathname);
    }
    await load();
});
</script>

<template>
    <section class="ga4gh-passport">
        <BreadcrumbHeading :items="breadcrumbItems" />

        <p v-localize>
            A GA4GH Passport (for example from NIH RAS) carries your data access grants. Galaxy sends it to data
            repositories when you fetch controlled-access data, for example a drs:// URI in the upload dialog.
        </p>

        <GAlert v-if="outcomeMessage" show variant="success" dismissible>{{ outcomeMessage }}</GAlert>
        <GAlert v-if="connectErrorMessage" show variant="danger" dismissible>{{ connectErrorMessage }}</GAlert>
        <GAlert :show="!!errorMessage" variant="danger" dismissible @dismissed="errorMessage = null">
            {{ errorMessage }}
        </GAlert>

        <GAlert v-if="loading" show variant="info">
            <LoadingSpan :message="localize('Loading your Passport')" />
        </GAlert>

        <template v-else-if="status">
            <GAlert v-if="!status.enabled" show variant="warning">
                {{ localize("This Galaxy is not configured for GA4GH Passports.") }}
            </GAlert>

            <div v-else-if="!status.identity" class="ga4gh-passport-not-connected">
                <p v-localize>You have not connected a Passport yet.</p>
                <GButton color="blue" @click="connect">
                    <FontAwesomeIcon :icon="faPlug" />
                    <span v-localize>Connect</span>
                </GButton>
            </div>

            <template v-else>
                <p class="ga4gh-passport-identity">
                    {{ localize("Your Galaxy account is linked to") }} <strong>{{ identityName }}</strong> ({{
                        status.identity.issuer
                    }}) {{ localize("since") }} <UtcDate :date="status.identity.bound_since" mode="pretty" />.
                </p>

                <div v-if="!status.passport" class="ga4gh-passport-missing">
                    <GAlert show variant="warning">
                        {{ localize("No valid Passport is stored: it expired or was forgotten.") }}
                    </GAlert>
                    <GButton color="blue" @click="connect">
                        <FontAwesomeIcon :icon="faSync" />
                        <span v-localize>Reconnect</span>
                    </GButton>
                </div>

                <div v-else class="ga4gh-passport-stored">
                    <dl class="ga4gh-passport-dates">
                        <dt v-localize>Issued</dt>
                        <dd><UtcDate :date="status.passport.issued_at" mode="pretty" /></dd>
                        <dt v-localize>Usable for data requests until</dt>
                        <dd :class="{ 'text-danger': usableSoonEnds }">
                            <UtcDate :date="status.passport.usable_until" mode="pretty" />
                            (<UtcDate :date="status.passport.usable_until" mode="elapsed" />)
                            <span v-if="usableSoonEnds" v-localize>- reconnect to renew it</span>
                        </dd>
                    </dl>

                    <h3 v-localize class="h-sm">Data access grants</h3>
                    <p v-if="!grants.length" v-localize>This Passport carries no data access grants.</p>
                    <table v-else class="table table-sm ga4gh-passport-grants">
                        <thead>
                            <tr>
                                <th v-localize>Study and consent group</th>
                                <th v-localize>Consent</th>
                                <th v-localize>Role</th>
                                <th v-localize>Grant expires</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="grant in grants" :key="grant.authorization">
                                <td>{{ grant.authorization }}</td>
                                <td>{{ grant.consent_name }}</td>
                                <td>{{ grant.role }}</td>
                                <td><UtcDate :date="grant.expiration" mode="pretty" /></td>
                            </tr>
                        </tbody>
                    </table>
                    <p v-if="unrecognizedVisas" class="text-muted">
                        {{ unrecognizedVisas }} {{ localize("Visa(s) of a type Galaxy does not use.") }}
                    </p>
                    <p v-if="status.passport.rejected_visas" class="text-muted">
                        {{ status.passport.rejected_visas }}
                        {{ localize("Visa(s) failed verification and are ignored.") }}
                    </p>

                    <GButton color="blue" @click="connect">
                        <FontAwesomeIcon :icon="faSync" />
                        <span v-localize>Reconnect</span>
                    </GButton>
                    <GButton color="red" outline :loading="forgetting" class="ga4gh-passport-forget" @click="forget">
                        <FontAwesomeIcon :icon="faTrash" />
                        <span v-localize>Forget</span>
                    </GButton>
                </div>

                <div class="ga4gh-passport-data-access mt-3">
                    <h3 v-localize class="h-sm">Access to controlled data in Galaxy</h3>
                    <p v-localize>
                        Data you fetch with your Passport is available to you only while you hold its grants in Galaxy.
                        Reconnecting renews them.
                    </p>
                    <p v-if="!status.data_access.length" v-localize>You hold no data access grants in Galaxy.</p>
                    <table v-else class="table table-sm">
                        <thead>
                            <tr>
                                <th v-localize>Study and consent group</th>
                                <th v-localize>Access until</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="access in status.data_access" :key="access.authorization">
                                <td>{{ access.authorization }}</td>
                                <td>
                                    <UtcDate :date="access.until" mode="pretty" />
                                    (<UtcDate :date="access.until" mode="elapsed" />)
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </template>
        </template>
    </section>
</template>

<style scoped lang="scss">
.ga4gh-passport-dates {
    display: grid;
    grid-template-columns: max-content auto;
    column-gap: 1rem;
}
</style>
