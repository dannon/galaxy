<script setup>
import { ref } from 'vue';
import { onMounted } from 'vue';

const name = "Template";
const count = ref(0);

const increment = () => {
  count.value++;
};

onMounted(() => {
  console.log('Hello World from my new viz component!');
  console.log('Options are ', props.options);
}); 

// define incoming props, just 'options':
const props = defineProps({
  options: {
    type: Object,
    required: true,
  },
});
</script>

<template>
  <div>
    <h1>My Fancy Component</h1>
    <p>{{ name }}</p>
    <p>{{ count }}</p>
    <code>{{ props.options }}</code>
  </div>
</template>