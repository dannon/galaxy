import { type Ref, ref } from "vue";

let toastRef: Ref = ref(null);

export const setToastComponentRef = (newRef: typeof toastRef) => {
    // eslint-disable-next-line vue/no-ref-as-operand
    toastRef = newRef;
};

/**
 * Direct export to simplify usage in Options Api component.
 * Use 'useToast' for the Composition Api instead.
 */
export const Toast = {
    success(message: string, title = "Success", href = "") {
        toastRef.value?.showToast(message, title, "success", href);
    },

    info(message: string, title = "Info", href = "") {
        toastRef.value?.showToast(message, title, "info", href);
    },

    warning(message: string, title = "Warning", href = "") {
        toastRef.value?.showToast(message, title, "warning", href);
    },

    error(message: string, title = "Error", href = "") {
        toastRef.value?.showToast(message, title, "danger", href);
    },
};

export function useToast() {
    return { ...Toast };
}
