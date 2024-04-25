import { useMagicKeys } from "@vueuse/core";
import { computed, onScopeDispose, reactive, ref, watch } from "vue";

import { useUserLocalStorage } from "@/composables/userLocalStorage";

import { defineScopedStore } from "./scopedStore";
import { type WorkflowCommentColor } from "./workflowEditorCommentStore";

export type CommentTool = "textComment" | "markdownComment" | "frameComment" | "freehandComment" | "freehandEraser";
export type EditorTool = "pointer" | CommentTool;
export type InputCatcherEventType =
    | "pointerdown"
    | "pointerup"
    | "pointermove"
    | "pointerleave"
    | "temporarilyDisabled";

interface InputCatcherEventListener {
    type: InputCatcherEventType;
    callback: (event: InputCatcherEvent) => void;
}

export interface InputCatcherEvent {
    type: InputCatcherEventType;
    position: [number, number];
}

export type WorkflowEditorToolbarStore = ReturnType<typeof useWorkflowEditorToolbarStore>;

export const useWorkflowEditorToolbarStore = defineScopedStore("workflowEditorToolbarStore", () => {
    const snapActive = useUserLocalStorage("workflow-editor-toolbar-snap-active", false);
    const currentTool = ref<EditorTool>("pointer");
    const inputCatcherActive = ref<boolean>(false);
    const inputCatcherEventListeners = new Set<InputCatcherEventListener>();
    const snapDistance = ref<10 | 20 | 50 | 100 | 200>(10);
    const toolbarVisible = useUserLocalStorage("workflow-editor-toolbar-visible", true);

    const commentOptions = reactive({
        bold: false,
        italic: false,
        color: "none" as WorkflowCommentColor,
        textSize: 2,
        lineThickness: 5,
        smoothing: 2,
    });

    const inputCatcherPressed = ref(false);

    function onInputCatcherEvent(type: InputCatcherEventType, callback: InputCatcherEventListener["callback"]) {
        const listener = {
            type,
            callback,
        };

        inputCatcherEventListeners.add(listener);

        onScopeDispose(() => {
            inputCatcherEventListeners.delete(listener);
        });
    }

    onInputCatcherEvent("pointerdown", () => (inputCatcherPressed.value = true));
    onInputCatcherEvent("pointerup", () => (inputCatcherPressed.value = false));
    onInputCatcherEvent("temporarilyDisabled", () => (inputCatcherPressed.value = false));

    watch(
        () => inputCatcherActive.value,
        () => {
            if (!inputCatcherActive.value) {
                inputCatcherPressed.value = false;
            }
        }
    );

    const { shift, space, alt, ctrl } = useMagicKeys();
    const inputCatcherEnabled = computed(() => !(shift?.value || space?.value || alt?.value || ctrl?.value));

    function emitInputCatcherEvent(type: InputCatcherEventType, event: InputCatcherEvent) {
        inputCatcherEventListeners.forEach((listener) => {
            if (listener.type === type) {
                listener.callback(event);
            }
        });
    }

    return {
        toolbarVisible,
        snapActive,
        snapDistance,
        currentTool,
        inputCatcherActive,
        inputCatcherEnabled,
        commentOptions,
        inputCatcherPressed,
        onInputCatcherEvent,
        emitInputCatcherEvent,
    };
});
