<script setup lang="ts">
import { BTableLite } from "bootstrap-vue";
</script>

<template>
    <BTableLite v-bind="$attrs" v-on="$listeners">
        <slot></slot>
    </BTableLite>
</template>
