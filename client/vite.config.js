import vue from '@vitejs/plugin-vue2'; // For Vue 2
import { defineConfig } from 'vite';
import tsconfigPaths from 'vite-tsconfig-paths';

export default defineConfig({
  plugins: [
    vue(),
    tsconfigPaths(),
  ],
  css: {
    preprocessorOptions: {
      scss: {
        //...
      },
    },
  },
  build: {
    rollupOptions: {
      minify: 'terser',
    },
    cssCodeSplit: true,
    sourcemap: true,
  },
});