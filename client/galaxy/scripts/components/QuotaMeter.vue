<template>
    <div>
        <div v-if="quota_percent" id="quota-meter" class="quota-meter progress">
            <b-progress class="quota-meter-text" :max="max"
                 data-placement="left"
                 style="top: 7px"
                 :title="title">
                <b-progress-bar :value="quota_percent">
                    <a href="https://galaxyproject.org/support/account-quotas/" target="_blank">
                        {{localizedUsing}} {{ quota_percent }} %
                    </a>
                </b-progress-bar>
            </b-progress>
        </div>
        <div v-else id="quota-meter" class="quota-meter" style="background-color: transparent">
            <div class="quota-meter-text"
                 data-placement="left"
                 data-original-title="This value is recalculated when you log out."
                 style="top: 6px; color: white">
                {{localizedUsing}} {{ nice_total_disk_usage }}
            </div>
        </div>
        <!-- <b-btn @click="clicked">TESTS!</b-btn> -->
    </div>
</template>
<script>
import _l from "utils/localization";
import Vue from "vue";
import BootstrapVue from "bootstrap-vue";

Vue.use(BootstrapVue);

export default {
    data() {
        return {
            quota_percent: null,
            max: 100,
            total_disk_usage: 10000000,
            nice_total_disk_usage: "100 GB"
        };
    },
    computed: {
        title() {
            return `${this.nice_total_disk_usage} Click for details.`;
        },
        localizedUsing() {
            return _l("Using");
        }
    },
    methods: {
        clicked() {
            this.quota_percent = Math.round(Math.random() * 100);
        },
        isOverQuota() {
            if (this.quota_percent === null || this.quota_percent >= this.max) {
                return false;
            } else {
                return true;
            }
        }
    }
};
</script>
