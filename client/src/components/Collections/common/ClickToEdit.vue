<script setup lang="ts">
import { faLevelDownAlt } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { BFormInput } from "bootstrap-vue";
import { computed, ref, watch } from "vue";

interface Props {
    value: string;
    title?: string;
    component?: string;
    noSaveOnBlur?: boolean;
    maxLines?: number;
}

const props = defineProps<Props>();

const emit = defineEmits<{
    (e: "input", value: string): void;
}>();

const clickToEditInput = ref<HTMLInputElement | null>(null);
const clickToEditLabel = ref<HTMLElement | null>(null);
const editable = ref(false);
const localValue = ref(props.value);

const computedValue = computed(() => props.value);

const labelClasses = computed(() => {
    const classes = ["click-to-edit-label", "text-break"];
    if (props.maxLines) {
        classes.push("click-to-edit-line-clamp");
    }
    return classes;
});

const isTextTruncated = computed(() => {
    if (!props.maxLines || !clickToEditLabel.value) {
        return false;
    }

    if (props.maxLines === 1) {
        return clickToEditLabel.value.scrollWidth > clickToEditLabel.value.clientWidth;
    } else {
        return clickToEditLabel.value.scrollHeight > clickToEditLabel.value.clientHeight;
    }
});

watch(
    () => editable.value,
    (value) => {
        if (!value) {
            emit("input", localValue.value);
        } else {
            setTimeout(() => {
                clickToEditInput.value?.focus();
            });
        }
    }
);

watch(
    () => props.value,
    (value) => {
        if (!editable.value) {
            localValue.value = value;
        }
    }
);

function onBlur() {
    if (props.noSaveOnBlur) {
        revertToOriginal();
    } else {
        editable.value = false;
    }
}

function revertToOriginal() {
    localValue.value = props.value;
    editable.value = false;
}
</script>

<template>
    <div v-if="editable" class="d-flex flex-gapx-1 input-icon-wrapper">
        <BFormInput
            id="click-to-edit-input"
            ref="clickToEditInput"
            v-model="localValue"
            class="w-100 input-with-icon"
            tabindex="0"
            title="Press enter/return to save, esc to revert changes"
            contenteditable
            max-rows="4"
            aria-label="Press enter/return to save, esc to revert changes"
            @blur.prevent.stop="onBlur"
            @keyup.prevent.stop.enter="editable = false"
            @keyup.prevent.stop.escape="revertToOriginal"
            @click.prevent.stop />
        <div class="input-icon">
            <FontAwesomeIcon :icon="faLevelDownAlt" class="enter-icon" />
        </div>
    </div>

    <component
        :is="props.component || 'label'"
        v-else
        ref="clickToEditLabel"
        v-b-tooltip.hover="isTextTruncated ? computedValue || title : null"
        role="button"
        for="click-to-edit-input"
        :class="labelClasses"
        :style="props.maxLines ? { '--max-lines': props.maxLines } : {}"
        tabindex="0"
        @keyup.enter="editable = true"
        @click.stop="editable = true">
        <span v-if="computedValue">{{ computedValue }}</span>
        <i v-else>{{ title }}</i>
    </component>
</template>

<style scoped lang="scss">
.click-to-edit-label {
    cursor: text;
    &:hover > * {
        text-decoration: underline;
    }

    &.click-to-edit-line-clamp {
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: var(--max-lines, 2);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: normal;

        span {
            display: block;
        }
    }
}

.input-icon-wrapper {
    position: relative;
}

.input-with-icon {
    padding-right: 15px;
}

.input-icon {
    position: absolute;
    right: 10px;
    top: 50%;
    transform: translateY(-50%);
    pointer-events: none;
}

.enter-icon {
    transform: rotate(90deg); // Rotates the arrow to look like the enter/return key
    opacity: 0.7;
}
</style>
