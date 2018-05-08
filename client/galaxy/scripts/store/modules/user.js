// initial state
const state = {
    quotaPercent: 14,
    totalDiskUsage: 10000000
};

// getters
const getters = {
    quotaPercent: () => state.quotaPercent,
    totalDiskUsage: () => state.totalDiskUsage
};

// actions
const actions = {};

// mutations
const mutations = {
    setQuotaPercent(state, quotaPercent) {
        state.quotaPercent = quotaPercent;
    },
    setTotalDiskUsage(state, totalDiskUsage) {
        state.totalDiskUsage = totalDiskUsage;
    }
};

export default {
    state,
    getters,
    actions,
    mutations
};
