<template>
    <div v-if="hasMessages" class="mx-3 my-2">
        <b-alert :show="history.isDeleted" variant="warning">
            {{ localized.isDeleted }}
        </b-alert>
        <b-alert :show="userOverQuota" variant="warning">
            {{ localized.userOverQuota }}
        </b-alert>
    </div>
</template>

<script>
export default {
    props: {
        history: { type: Object, required: true },
    },
    data() {
        return {
            userOverQuota: false,
        };
    },
    computed: {
        hasMessages() {
            return this.userOverQuota || history.isDeleted;
        },
        localized() {
            return {
                isDeleted: "This history has been deleted",
                userOverQuota:
                    "You are over your disk quota. Tool execution is on hold until your disk usage drops below your allocated quota.",
            };
        },
    },
};
</script>
