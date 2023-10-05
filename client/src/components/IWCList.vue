<script setup lang="ts">
import { onMounted, ref } from "vue";

import Heading from "@/components/Common/Heading.vue";

interface Collection {
    name: string;
    id: number;
    displayName: string;
    topic: string;
    entries: Entry[];
}

interface Entry {
    name: string;
    id: number;
    entryPath: string;
    // whatever else we need
}

// TODO: this should be a reactive store instead eventually, but for just getting it working...

const collections = ref<Collection[]>([]);

onMounted(async () => {
    try {
        // iwc organization collections
        const response = await fetch("https://dockstore.org/api/organizations/33/collections");
        const data = await response.json();
        collections.value = await Promise.all(
            data.map(async (collection: Collection) => {
                const url = `https://dockstore.org/api/organizations/iwc/collections/${collection.name}/name`;
                const response = await fetch(url);
                const contents = await response.json();
                const entries = contents.entries || [];
                const updatedEntries = await Promise.all(
                    entries.map(async (entry: Entry) => {
                        const url = `https://dockstore.org/api/workflows/path/workflow/${encodeURIComponent(
                            entry.entryPath
                        )}/published?include=validations%2Cauthors&subclass=BIOWORKFLOW`;
                        const response = await fetch(url);
                        const contents = await response.json();

                        // smash latest version attributes to the top level for convenience
                        const latestVersion = contents.workflowVersions[0];

                        return { ...entry, ...contents, latestVersion: latestVersion };
                    })
                );
                return { ...collection, entries: updatedEntries };
            })
        );
    } catch (error) {
        console.error(error);
    }
});
</script>

<template>
    <div>
        <Heading h1>IWC Workflows</Heading>
        <p>
            The IWC maintains high-quality Galaxy Workflows Workflows are categorized in the workflows directory, and
            listed in Dockstore and WorkflowHub. All workflows are reviewed and tested before publication and with every
            new Galaxy release. Deposited workflows follow best practices and are versioned using github releases.
        </p>
        <div v-for="collection in collections" :key="collection.id">
            <Heading separator h2>
                {{ collection.displayName }}
            </Heading>
            <!-- <Heading h3 size="sm">{{ collection.topic }}</Heading> -->
            <div>
                <p v-for="entry in collection.entries" :key="entry.id">
                    {{ entry.id }}
                    <b>{{ entry.repository }}</b>
                    {{ entry.latestVersion.description }}
                </p>
            </div>
        </div>
    </div>
</template>

<style scoped>
/* add your styles here */
</style>
