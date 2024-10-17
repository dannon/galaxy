<template>
    <b-table small caption-top :fields="['label', 'parameter_value']" :items="parameters" />
</template>
<script>
export default {
    props: {
        parameters: { type: Array, required: true },
    },
};
</script>
