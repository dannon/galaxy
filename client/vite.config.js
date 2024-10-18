import vue from '@vitejs/plugin-vue2'; // For Vue 2
import { defineConfig } from 'vite';
import tsconfigPaths from 'vite-tsconfig-paths';
import path from 'path';

const targetEnv = process.env.NODE_ENV == "production" ? "production" : "development";

const scriptsBase = path.resolve(__dirname, "./src");
const styleBase = path.join(scriptsBase, "style");

export default defineConfig({
  plugins: [
    vue(),
    tsconfigPaths(),
  ],
  css: {
    preprocessorOptions: {
      scss: {
        quietDeps: true,
        includePaths: [
          path.join(styleBase, "scss"),
          path.resolve(__dirname, "./node_modules"),
        ],
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
    minify: 'terser',
    rollupOptions: {
      input: 'src/entry/analysis/index.ts',
    },
    cssCodeSplit: true,
    sourcemap: true,
  },
});