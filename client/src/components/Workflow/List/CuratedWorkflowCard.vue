<script setup lang="ts">
import { faExternalLinkAlt, faPlay, faUpload } from "@fortawesome/free-solid-svg-icons";
import { storeToRefs } from "pinia";
import { computed } from "vue";
import { useRouter } from "vue-router/composables";

import type { CuratedWorkflow } from "@/api/curatedWorkflows";
import type { CardAction, CardBadge } from "@/components/Common/GCard.types";
import { getRedirectOnImportPath } from "@/components/Workflow/redirectPath";
import { Services } from "@/components/Workflow/services";
import { copyWorkflow } from "@/components/Workflow/workflows.services";
import { Toast } from "@/composables/toast";
import { useUserStore } from "@/stores/userStore";

import GCard from "@/components/Common/GCard.vue";

interface Props {
    workflow: CuratedWorkflow;
    gridView?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
    gridView: false,
});

const emit = defineEmits<{
    (e: "tagClick", tag: string): void;
}>();

const router = useRouter();
const services = new Services();

const userStore = useUserStore();
const { isAnonymous } = storeToRefs(userStore);

const workflow = computed(() => props.workflow);

/** Presence of a stored workflow id means the row is a workflow hosted here, not a catalog entry. */
const isLocal = computed(() => Boolean(workflow.value.stored_workflow_id));

const importTitle = computed(() =>
    isAnonymous.value ? "Log in to import this workflow" : "Import this workflow into your account",
);

const titleBadges = computed<CardBadge[]>(() =>
    (workflow.value.collections ?? []).map((collection: string) => ({
        id: `curated-collection-${collection}`,
        label: collection,
        title: `Part of the ${collection} collection`,
        type: "badge",
        variant: "outline-secondary",
    })),
);

const badges = computed<CardBadge[]>(() => {
    const cardBadges: CardBadge[] = [];

    if (workflow.value.number_of_steps !== null && workflow.value.number_of_steps !== undefined) {
        cardBadges.push({
            id: "curated-steps",
            label: `${workflow.value.number_of_steps} steps`,
            title: "Number of steps in this workflow",
        });
    }

    if (workflow.value.release) {
        cardBadges.push({
            id: "curated-release",
            label: `v${workflow.value.release}`,
            title: "Released version of this workflow",
        });
    }

    return cardBadges;
});

/** A catalog row can only be imported if the catalog gave us something to import. */
const canImport = computed(() => (isLocal.value ? true : Boolean(workflow.value.trs_tool_id)));

const primaryActions = computed<CardAction[]>(() => {
    const importAction: CardAction = {
        id: "curated-import",
        label: "Import",
        icon: faUpload,
        title: canImport.value ? importTitle.value : "This workflow cannot be imported automatically",
        disabled: isAnonymous.value || !canImport.value,
        variant: "outline-primary",
        handler: isLocal.value ? onImportLocal : onImportTrs,
    };

    if (!isLocal.value) {
        return [importAction];
    }

    return [
        {
            id: "curated-run",
            label: "Run",
            icon: faPlay,
            title: "Run workflow",
            to: `/workflows/run?id=${workflow.value.stored_workflow_id}`,
        },
        importAction,
    ];
});

const extraActions = computed<CardAction[]>(() => {
    if (isLocal.value) {
        return [
            {
                id: "curated-open",
                label: "View workflow",
                title: "View this workflow",
                to: `/published/workflow?id=${workflow.value.stored_workflow_id}`,
            },
        ];
    }

    const actions: CardAction[] = [
        {
            id: "curated-external-link",
            label: "View on iwc.galaxyproject.org",
            title: "View this workflow on iwc.galaxyproject.org",
            icon: faExternalLinkAlt,
            externalLink: true,
            href: workflow.value.external_url ?? undefined,
        },
    ];

    if (workflow.value.doi) {
        actions.push({
            id: "curated-doi",
            label: "View DOI",
            title: `Resolve DOI ${workflow.value.doi}`,
            icon: faExternalLinkAlt,
            externalLink: true,
            href: `https://doi.org/${workflow.value.doi}`,
        });
    }

    return actions;
});

async function onImportTrs() {
    try {
        const response = await services.importTrsTool(
            workflow.value.trs_server,
            workflow.value.trs_tool_id,
            workflow.value.trs_version_id,
        );
        router.push(getRedirectOnImportPath(response));
    } catch (error) {
        Toast.error(`Failed to import workflow: ${error}`);
    }
}

async function onImportLocal() {
    try {
        await copyWorkflow(workflow.value.stored_workflow_id as string, workflow.value.owner ?? undefined);
        Toast.success("Workflow imported successfully");
    } catch (error) {
        Toast.error(`Failed to import workflow: ${error}`);
    }
}
</script>

<template>
    <GCard
        :id="workflow.id"
        class="curated-workflow-card"
        :title="workflow.name"
        :title-badges="titleBadges"
        :title-n-lines="2"
        :can-rename-title="false"
        :description="workflow.description"
        :grid-view="props.gridView"
        :badges="badges"
        :extra-actions="extraActions"
        :primary-actions="primaryActions"
        :selectable="false"
        :show-bookmark="false"
        :tags="workflow.tags"
        :tags-editable="false"
        :max-visible-tags="props.gridView ? 2 : 8"
        :update-time="workflow.update_time ?? ''"
        @tagClick="(tag) => emit('tagClick', tag)" />
</template>
