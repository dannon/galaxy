<template>
    <GTable small caption-top :fields="['label', 'parameter_value']" :items="parameters" />
</template>
<script>
import BootstrapVue from "bootstrap-vue";
import Vue from "vue";

import GTable from "@/component-library/GTable.vue";

Vue.use(BootstrapVue);

export default {
    components: {
        GTable,
    },
    props: {
        parameters: { type: Array, required: true },
    },
};
</script>
