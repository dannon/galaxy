<script setup lang="ts">
import { BTable } from "bootstrap-vue";
</script>

<template>
    <BTable v-bind="$attrs" v-on="$listeners">
        <slot></slot>
    </BTable>
</template>
