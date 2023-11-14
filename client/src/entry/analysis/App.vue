<script setup>
import { getAppRoot } from "onload";
import { storeToRefs } from "pinia";
import { withPrefix } from "utils/redirect";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute,useRouter } from "vue-router";

import { getGalaxyInstance } from "@/app";
import ConfirmDialog from "@/components/ConfirmDialog";
import { HistoryPanelProxy } from "@/components/History/adapters/HistoryPanelProxy";
import Toast from "@/components/Toast";
import { setConfirmDialogComponentRef } from "@/composables/confirmDialog";
import { setGlobalUploadModal } from "@/composables/globalUploadModal";
import { useRouteQueryBool } from "@/composables/route";
import { setToastComponentRef } from "@/composables/toast";
import { fetchMenu } from "@/entry/analysis/menu";
import { WindowManager } from "@/layout/window-manager";
import Modal from "@/mvc/ui/ui-modal";
import { useHistoryStore } from "@/stores/historyStore";
import { useNotificationsStore } from "@/stores/notificationsStore";
import { useUserStore } from "@/stores/userStore";

import Alert from "@/components/Alert.vue";
import DragGhost from "@/components/DragGhost.vue";
import Masthead from "@/components/Masthead/Masthead.vue";
import BroadcastsOverlay from "@/components/Notifications/Broadcasts/BroadcastsOverlay.vue";
import UploadModal from "@/components/Upload/UploadModal.vue";

const userStore = useUserStore();
const { currentTheme } = storeToRefs(userStore);
const { currentHistory } = storeToRefs(useHistoryStore());

const toastRef = ref(null);
setToastComponentRef(toastRef);

const confirmDialogRef = ref(null);
setConfirmDialogComponentRef(confirmDialogRef);

const uploadModal = ref(null);
setGlobalUploadModal(uploadModal);

const embedded = useRouteQueryBool("embed");
const showBroadcasts = computed(() => !embedded.value);
const showAlerts = computed(() => !embedded.value);

const config = ref(getGalaxyInstance().config);
const confirmation = ref(null);
const resendUrl = `${getAppRoot()}user/resend_verification`;
const windowManager = new WindowManager();

const router = useRouter();
const route = useRoute();

const Galaxy = getGalaxyInstance();



function startNotificationsPolling() {
    const notificationsStore = useNotificationsStore();
    notificationsStore.startPollingNotifications();
}

function openUrl(urlObj) {
    if (!urlObj.target) {
        router.push(urlObj.url);
    } else {
        const url = withPrefix(urlObj.url);
        if (urlObj.target == "_blank") {
            window.open(url);
        } else {
            window.location = url;
        }
    }
}
const tabs = computed(() => {
    return fetchMenu(config);
});

const showMasthead = computed(() => {
    if (embedded.value) {
        return false;
    }
    console.debug(route);
    const masthead = route.query.hide_masthead;
    if (masthead !== undefined) {
        return masthead.toLowerCase() != "true";
    }
    return true;
});

const theme = computed(() => {
    if (embedded.value) {
        return null;
    }

    const themeKeys = Object.keys(config.value.themes);
    if (themeKeys.length > 0) {
        const foundTheme = themeKeys.includes(currentTheme.value);
        const selectedTheme = foundTheme ? currentTheme.value : themeKeys[0];
        return config.value.themes[selectedTheme];
    }
    return null;
});

const windowTab = computed(() => {
    return windowManager.getTab();
});

watch(
    () => embedded.value,
    () => {
        if (embedded.value) {
            userStore.$reset();
        } else {
            userStore.loadUser();
        }
    },
    { immediate: true }
);

watch(
    () => confirmation.value,
    () => {
        if (confirmation.value) {
            console.debug("App - Confirmation before route change: ", confirmation.value);
            router.confirmation = confirmation.value;
        }
    }
);

watch(
    () => currentHistory.value,
    () => {
        Galaxy.currHistoryPanel?.syncCurrentHistoryModel(currentHistory.value);
    }
);

onMounted(() => {
    Galaxy.currHistoryPanel = new HistoryPanelProxy();
    Galaxy.modal = new Modal.View();
    Galaxy.frame = windowManager;
    if (Galaxy.config.enable_notification_system) {
        startNotificationsPolling();
    }
    window.onbeforeunload = () => {
        if (confirmation.value || windowManager.beforeUnload()) {
            return "Are you sure you want to leave the page?";
        }
    };
});
</script>

<template>
    <div id="app" :style="theme">
        <div id="everything">
            <div id="background" />
            <Masthead
                v-if="showMasthead"
                id="masthead"
                :brand="config.brand"
                :logo-url="config.logo_url"
                :logo-src="theme?.['--masthead-logo-img'] ?? config.logo_src"
                :logo-src-secondary="theme?.['--masthead-logo-img-secondary'] ?? config.logo_src_secondary"
                :tabs="tabs"
                :window-tab="windowTab"
                @open-url="openUrl" />
            <Alert
                v-if="showAlerts && config.message_box_visible && config.message_box_content"
                id="messagebox"
                class="rounded-0 m-0 p-2"
                :variant="config.message_box_class || 'info'">
                <span class="fa fa-fw mr-1 fa-exclamation" />
                <!-- eslint-disable-next-line vue/no-v-html -->
                <span v-html="config.message_box_content"></span>
            </Alert>
            <Alert
                v-if="showAlerts && config.show_inactivity_warning && config.inactivity_box_content"
                id="inactivebox"
                class="rounded-0 m-0 p-2"
                variant="warning">
                <span class="fa fa-fw mr-1 fa-exclamation-triangle" />
                <span>{{ config.inactivity_box_content }}</span>
                <span>
                    <a class="ml-1" :href="resendUrl">Resend Verification</a>
                </span>
            </Alert>
            <router-view @update:confirmation="confirmation = $event" />
        </div>
        <div id="dd-helper" />
        <Toast ref="toastRef" />
        <ConfirmDialog ref="confirmDialogRef" />
        <UploadModal ref="uploadModal" />
        <BroadcastsOverlay v-if="showBroadcasts" />
        <DragGhost />
    </div>
</template>

<style lang="scss">
@import "custom_theme_variables.scss";
</style>
