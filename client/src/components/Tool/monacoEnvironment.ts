/**
 * One-time Monaco setup: the worker factory Vite bundles and the editor instance the
 * `@guolao/vue-monaco-editor` loader hands out. Imported for its side effects by any
 * component that mounts a Monaco editor.
 */
import { loader } from "@guolao/vue-monaco-editor";
import * as monaco from "monaco-editor";
// Import workers using Vite's ?worker syntax for proper bundling
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import yamlWorker from "monaco-yaml/yaml.worker?worker";

self.MonacoEnvironment = {
    getWorker(_workerId: string, label: string) {
        if (label === "yaml") {
            return new yamlWorker();
        }
        if (label === "json") {
            return new jsonWorker();
        }
        if (label === "typescript" || label === "javascript") {
            return new tsWorker();
        }
        return new editorWorker();
    },
};

// loaded monaco-editor from `node_modules`
loader.config({ monaco });
