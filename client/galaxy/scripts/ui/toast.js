import "scss/toast.scss";

import Vue from "vue";
import Toasted from "vue-toasted";

const toastOptions = {
    base: {
        iconPack: "fontawesome",
        duration: 5000,
        action: {
            text: "close",
            onClick: (e, t) => {
                t.goAway(0);
            }
        }
        //theme: "bubble",
    },
    success: {
        type: "success",
        icon: "check"
    },
    info: {
        type: "info",
        icon: "info"
    },
    error: {
        type: "error",
        icon: "times"
    },
    warning: {
        type: "warning",
        icon: "warning"
    }
};

export class VueToasted {
    constructor(options) {
        Vue.use(Toasted, toastOptions);
    }
    toast(msg, options = {}) {
        options = { ...toastOptions.base, ...options };
        Vue.toasted.show(msg, options);
    }
    success(msg, options = {}) {
        options = { ...toastOptions.success, ...options };
        return this.toast(msg, options);
    }
    info(msg, options = {}) {
        options = { ...toastOptions.info, ...options };
        return this.toast(msg, options);
    }
    error(msg, options = {}) {
        options = { ...toastOptions.error, ...options };
        return this.toast(msg, options);
    }
    warning(msg, options = {}) {
        options = { ...toastOptions.warning, ...options };
        return this.toast(msg, options);
    }
}

export const Toast = new VueToasted();
