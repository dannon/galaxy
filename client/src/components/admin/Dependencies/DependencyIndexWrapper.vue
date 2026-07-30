<template>
    <div>
        <GAlert v-if="error" show variant="danger">{{ error }}</GAlert>
        <div v-else>
            <slot name="header"></slot>
            <LoadingSpan v-if="loading" :message="loadingMessage" />
            <div v-else>
                <slot name="actions"></slot>
                <slot name="body"></slot>
                <slot name="actions"></slot>
            </div>
        </div>
    </div>
</template>
<script>
import GAlert from "@/components/BaseComponents/GAlert.vue";
import LoadingSpan from "@/components/LoadingSpan.vue";

export default {
    components: {
        GAlert,
        LoadingSpan,
    },
    props: {
        loading: {
            type: Boolean,
            required: true,
        },
        loadingMessage: {
            type: String,
            required: true,
        },
        error: {
            type: String,
            default: null,
        },
    },
};
</script>
