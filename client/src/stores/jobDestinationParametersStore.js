import axios from "axios";
import { defineStore } from "pinia";
import { getAppRoot } from "onload/loadConfig";

export const useJobDestinationParametersStore = defineStore("jobDestinationParametersStore", {
    state: () => ({
        jobDestinationParametersByJobId: {},
    }),
    getters: {
        jobDestinationParams: (state) => (jobId) => {
            return state.jobDestinationParametersByJobId[jobId] || [];
        },
    },
    actions: {
        async fetchJobDestinationParameters(jobId) {
            const { data } = await axios.get(`${getAppRoot()}api/jobs/${jobId}/destination_params`);
            this.$patch(state => {
                state.jobDestinationParametersByJobId[jobId] = data;
            });
        },
    },
});