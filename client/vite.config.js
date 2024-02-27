import ViteYaml from "@modyfi/vite-plugin-yaml";
import vue from "@vitejs/plugin-vue2"; // For Vue 2
import path from "path";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

import { license } from "./package.json";

const targetEnv = process.env.NODE_ENV == "production" ? "production" : "development";
const scriptsBase = path.resolve(__dirname, "./src");
const styleBase = path.join(scriptsBase, "style");
const buildDate = new Date();


console.debug(license);
export default defineConfig({
    define: {
        __targetEnv__: JSON.stringify(targetEnv),
        __buildTimestamp__: JSON.stringify(buildDate.toISOString()),
        __license__: JSON.stringify(license),
        "process.env": {},
    },
    plugins: [vue(), tsconfigPaths(), ViteYaml()],
    css: {
        preprocessorOptions: {
            scss: {
                quietDeps: true,
                includePaths: [styleBase, path.join(styleBase, "scss"), path.resolve(__dirname, "./node_modules")],
            },
        },
    },
    resolve: {
        alias: {
            "@": scriptsBase,
            config: path.resolve(scriptsBase, "config", targetEnv) + ".js",
        },
    },
    build: {
        manifest: "true",
        rollupOptions: {
            input: "src/entry/analysis/index.ts",
            output: {
                // Specify the output file name here
                entryFileNames: "[name].js", // For entry chunks
                chunkFileNames: "[name]-[hash].js", // For non-entry chunks
                assetFileNames: "[name]-[hash][extname]", // For assets like images and css
            },
        },
        cssCodeSplit: true,
        sourcemap: true,
    },
});
