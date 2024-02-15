import vue from '@vitejs/plugin-vue2'; // For Vue 2
import { defineConfig } from 'vite';
import tsconfigPaths from 'vite-tsconfig-paths';
import path from 'path';

const targetEnv = process.env.NODE_ENV == "production" ? "production" : "development";

const scriptsBase = path.resolve(__dirname, "./src");
export default defineConfig({
  plugins: [
    vue(),
    tsconfigPaths(),
  ],
  css: {
    preprocessorOptions: {
      scss: {
        // Do we need includePaths?
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
        config: path.resolve(scriptsBase, "config", targetEnv) + ".js",
    },
  },
  build: {
    manifest: 'true',
    rollupOptions: {
      minify: 'terser',
      input: 'src/entry/analysis/index.ts',
    },
    cssCodeSplit: true,
    sourcemap: true,
  },
});