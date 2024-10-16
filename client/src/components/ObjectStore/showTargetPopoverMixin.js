import ShowSelectedObjectStore from "./ShowSelectedObjectStore";

export default {
    components: {
        ShowSelectedObjectStore,
    },
    props: {
        titleSuffix: {
            type: String,
            default: null,
        },
    },
    computed: {
        title() {
            return this.l(`Preferred Target Storage Location ${this.titleSuffix || ""}`);
        },
    },
};
