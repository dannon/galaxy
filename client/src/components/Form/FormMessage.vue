<template>
    <b-alert class="mt-2" :variant="variant" :show="showAlert">
        {{ localized.message }}
    </b-alert>
</template>
<script>
import { localize } from "@/utils/localization";

export default {
    props: {
        message: {
            type: String,
            default: null,
        },
        variant: {
            type: String,
            default: "info",
        },
        timeout: {
            type: Number,
            default: 3000,
        },
        persistent: {
            type: Boolean,
            default: false,
        },
    },
    data() {
        return {
            showDismissable: false,
        };
    },
    computed: {
        showAlert() {
            if (this.message) {
                if (!this.persistent) {
                    return this.showDismissable;
                }
                return true;
            }
            return false;
        },
        localized() {
            return {
                message: localize(this.message),
            };
        },
    },
    watch: {
        message(newMessage) {
            this.resetTimer();
        },
    },
    methods: {
        resetTimer() {
            if (this.message) {
                this.showDismissable = true;
                if (this.timer) {
                    clearTimeout(this.timer);
                }
                this.timer = setTimeout(() => {
                    this.showDismissable = false;
                }, this.timeout);
            }
        },
    },
};
</script>
