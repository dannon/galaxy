<template>
    <div>
        <div v-if="quotaPercent" id="quota-meter" class="quota-meter progress">
            <b-progress class="quota-meter-text" :max="max"
                 data-placement="left"
                 style="top: 7px"
                 :title="title"
                 :variant="usageVariant">
                <b-progress-bar :value="quotaPercent">
                    <a href="https://galaxyproject.org/support/account-quotas/" target="_blank">
                        {{ localized("Using") }} {{ quotaPercent }} %
                    </a>
                </b-progress-bar>
            </b-progress>
        </div>
        <div v-else id="quota-meter" class="quota-meter" style="background-color: transparent">
            <div class="quota-meter-text"
                 data-placement="left"
                 data-original-title="This value is recalculated when you log out."
                 style="top: 6px; color: white">
                {{ localized("Using") }} {{ nice_total_disk_usage }}
            </div>
        </div>
    </div>
</template>
<script>
import _l from "utils/localization";
import Vue from "vue";
import BootstrapVue from "bootstrap-vue";
import { mapGetters } from "vuex";

Vue.use(BootstrapVue);

export default {
    data() {
        return {
            max: 100,
            nice_total_disk_usage: "100 GB",
            warnAtPercent: 85,
            errorAtPercent: 100
        };
    },
    computed: {
        ...mapGetters(["quotaPercent", "totalDiskUsage"]),
        title() {
            return `${this.nice_total_disk_usage} Click for details.`;
        },
        usageVariant() {
            if (this.quotaPercent >= this.errorAtPercent) {
                return "danger";
            } else if (this.quotaPercent >= this.warnAtPercent) {
                return "warning";
            } else {
                return "success";
            }
        }
    },
    methods: {
        localized(text) {
            return _l(text);
        },
        isOverQuota() {
            if (this.quotaPercent === null || this.quotaPercent >= this.max) {
                return false;
            } else {
                return true;
            }
        }
    }
};
</script>
