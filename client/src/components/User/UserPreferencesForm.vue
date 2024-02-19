<template>
    <FormGeneric v-bind="formConfig" />
</template>
<script>
import { getUserPreferencesModel } from "@/components/User/UserPreferencesModel";
import Query from "@/utils/query-string-parsing";

import FormGeneric from "@/components/Form/FormGeneric.vue";

export default {
    components: {
        FormGeneric,
    },
    props: {
        formId: {
            type: String,
            required: true,
        },
    },
    computed: {
        formConfig() {
            const userId = Query.get("id");
            const model = getUserPreferencesModel(userId);
            return model[this.formId];
        },
    },
};
</script>
