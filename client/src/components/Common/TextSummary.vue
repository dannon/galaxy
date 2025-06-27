<script setup lang="ts">
import { faChevronDown, faChevronUp } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { computed, ref } from "vue";

interface Props {
    /** The maximum length of the unexpanded text / summary */
    maxLength?: number;
    /** The text to summarize */
    description: string;
    /** Number of lines to show when using line-based truncation */
    maxLines?: number;
    /** If `true`, doesn't show expand/collapse buttons */
    noExpand?: boolean;
    /** The component to use for the summary, default = `<p>` */
    component?: string;
    /** If `true`, shows the full text */
    showExpandText?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
    maxLength: 150,
    component: "span",
    showExpandText: true,
});

const showDetails = ref(false);
const refOneLineSummary = ref<HTMLElement | null>(null);
const refMultiLineSummary = ref<HTMLElement | null>(null);

const useLineTruncation = computed(() => !!props.maxLines);

const textTooLong = computed(() => {
    if (useLineTruncation.value) {
        // Line-based truncation using maxLines
        const ref = props.maxLines === 1 ? refOneLineSummary.value : refMultiLineSummary.value;
        if (ref) {
            if (props.maxLines === 1) {
                return ref.scrollWidth > ref.clientWidth;
            } else {
                return ref.scrollHeight > ref.clientHeight;
            }
        }
        return false;
    } else {
        // Character-based truncation (default behavior)
        return props.description.length > props.maxLength;
    }
});
</script>

<template>
    <div
        class="text-summary"
        :class="{
            'text-summary-short': !showDetails && props.maxLines === 1,
            'text-summary-multi-line': !showDetails && useLineTruncation && props.maxLines !== 1,
        }">
        <component
            :is="props.component"
            :ref="props.maxLines === 1 ? 'refOneLineSummary' : 'refMultiLineSummary'"
            :style="useLineTruncation && props.maxLines !== 1 && !showDetails ? { '--max-lines': props.maxLines } : {}">
            <div class="html-paragraph d-inline-block overflow-hidden w-100" v-html="props.description" />
        </component>

        <span
            v-if="!noExpand && textTooLong"
            v-b-tooltip.hover
            class="text-summary-expand-button"
            :class="{ 'text-summary-expand-float': !props.showExpandText }"
            :title="showDetails ? 'Show less' : 'Show more'"
            role="button"
            tabindex="0"
            @keyup.enter="showDetails = !showDetails"
            @click="showDetails = !showDetails">
            <template v-if="showExpandText">
                <template v-if="showDetails">Show less</template>
                <template v-else>Show more</template>
            </template>

            <FontAwesomeIcon :icon="showDetails ? faChevronUp : faChevronDown" />
        </span>
    </div>
</template>

<style scoped lang="scss">
@import "theme/blue.scss";

.text-summary {
    &.text-summary-short {
        .html-paragraph {
            text-overflow: ellipsis;
            white-space: nowrap;

            :deep(p) {
                white-space: nowrap;
                margin: 0;
            }
        }
    }

    &:deep(p) {
        margin: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        width: 100%;
        margin: 0;

        &:not(:first-child) {
            display: none;
        }
    }

    &.text-summary-multi-line {
        .html-paragraph {
            display: -webkit-box;
            -webkit-box-orient: vertical;
            -webkit-line-clamp: var(--max-lines, 2);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: normal;

            :deep(p) {
                white-space: normal;
                margin: 0;
                display: block;

                &:not(:first-child) {
                    display: block;
                }
            }
        }
    }
}

.text-summary-expand-button {
    cursor: pointer;
    width: fit-content;
    float: right;
    color: $text-light;
    margin-left: auto;

    .text-summary-expand-float {
        position: absolute;
        right: 5px;
        bottom: 0;
    }
}
</style>
