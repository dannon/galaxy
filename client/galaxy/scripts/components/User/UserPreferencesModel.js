import { getGalaxyInstance } from "app";
import _l from "utils/localization";

export const getUserPreferencesModel = () => {
    const Galaxy = getGalaxyInstance();
    const config = Galaxy.config;
    return {
        information: {
            title: _l("Manage Information"),
            description: "Edit your email, addresses and custom parameters or change your public name.",
            url: `api/users/${Galaxy.user.id}/information/inputs`,
            icon: "fa-user",
            redirect: "user",
            shouldRender: !config.use_remote_user
        },
        password: {
            title: _l("Change Password"),
            description: _l("Allows you to change your login credentials."),
            icon: "fa-unlock-alt",
            url: `api/users/${Galaxy.user.id}/password/inputs`,
            submit_title: "Save password",
            redirect: "user",
            shouldRender: !config.use_remote_user
        },
        external_ids: {
            title: _l("Manage Third-Party Identities"),
            description: _l("Connect or disconnect access to your third-party identities."),
            icon: "fa-id-card-o",
            submit_title: "Disconnect identity",
            submit_icon: "fa-trash"
        },
        communication: {
            title: _l("Change Communication Settings"),
            description: _l("Enable or disable the communication feature to chat with other users."),
            url: `api/users/${Galaxy.user.id}/communication/inputs`,
            icon: "fa-comments-o",
            redirect: "user",
            shouldRender: !!config.enable_communication_server
        },
        permissions: {
            title: _l("Set Dataset Permissions for New Histories"),
            description:
                "Grant others default access to newly created histories. Changes made here will only affect histories created after these settings have been stored.",
            url: `api/users/${Galaxy.user.id}/permissions/inputs`,
            icon: "fa-users",
            submit_title: "Save permissions",
            redirect: "user"
        },
        make_data_private: {
            title: _l("Make All Data Private"),
            description: _l("Click here to make all data private."),
            icon: "fa-lock"
        },
        api_key: {
            title: _l("Manage API Key"),
            description: _l("Access your current API key or create a new one."),
            url: `api/users/${Galaxy.user.id}/api_key/inputs`,
            icon: "fa-key",
            submit_title: "Create a new key",
            submit_icon: "fa-check"
        },
        cloud_auth: {
            title: _l("Manage Cloud Authorization"),
            description: _l("Add or modify the configuration that grants Galaxy to access your cloud-based resources."),
            icon: "fa-cloud",
            submit_title: "Create a new key",
            submit_icon: "fa-check"
        },
        toolbox_filters: {
            title: _l("Manage Toolbox Filters"),
            description: _l("Customize your Toolbox by displaying or omitting sets of Tools."),
            url: `api/users/${Galaxy.user.id}/toolbox_filters/inputs`,
            icon: "fa-filter",
            submit_title: "Save filters",
            redirect: "user",
            shouldRender: !!config.has_user_tool_filters
        },
        custom_builds: {
            title: _l("Manage Custom Builds"),
            description: _l("Add or remove custom builds using history datasets."),
            icon: "fa-cubes"
        },
        genomespace: {
            title: _l("Request GenomeSpace Token"),
            description: _l("Requests token through OpenID."),
            icon: "fa-openid"
        },
        logout: {
            title: _l("Sign Out"),
            description: _l("Click here to sign out of all sessions."),
            icon: "fa-sign-out",
            shouldRender: !!Galaxy.session_csrf_token
        }
    };
};
