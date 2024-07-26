<template>
    <FormGeneric v-bind="formConfig" />
</template>
<script>
import FormGeneric from "@/components/Form/FormGeneric";
import Query from "@/utils/query-string-parsing";

import { getUserPreferencesModel } from "./UserPreferencesModel";

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
