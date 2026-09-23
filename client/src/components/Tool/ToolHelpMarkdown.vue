<script setup lang="ts">
import { computed, ref } from "vue";

import { useGxUris } from "@/components/Markdown/gxuris";
import { markup } from "@/components/ObjectStore/configurationMarkdown";
import { useFormattedToolHelp } from "@/composables/formattedToolHelp";

const props = defineProps<{
    content: string;
    syntaxHighlighter?: (code: string, language: string, languageAttributes: string) => string;
}>();

const markdownHtml = computed(() => markup(props.content ?? "", false, props.syntaxHighlighter));
// correct links and header information... this should work the same between rst and
// markdown entirely I think.
const { formattedContent } = useFormattedToolHelp(markdownHtml);

const helpHtml = ref<HTMLDivElement>();

const { internalHelpReferences, MarkdownHelpPopovers } = useGxUris(helpHtml);
</script>

<template>
    <span>
        <div ref="helpHtml" v-safe-html="{ html: formattedContent, profile: 'markdown' }" />
        <MarkdownHelpPopovers :elements="internalHelpReferences" />
    </span>
</template>
