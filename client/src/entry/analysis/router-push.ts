import { type RouteLocationRaw,type Router } from "vue-router";

import { getGalaxyInstance } from "@/app";
import { addSearchParams } from "@/utils/url";

/**
 * Is called before the regular router.push() and allows us to provide logs,
 * handle the window manager, avoid duplication warnings, and force a component
 * refresh if needed.
 *
 * @param {String} Location as parsed to original router.push()
 * @param {Object} Custom options, to provide a title and/or force reload
 */
export function patchRouterPush(router: Router) {
    const originalPush = router.push;
    router.push = function(location: RouteLocationRaw, options: any = {}) {
        // Add key to location to force component refresh
        const { title, force } = options;
        if (force) {
            location = addSearchParams(location, { __vkey__: Date.now() });
        }
        /*
        // Verify if confirmation is required
        if (this.confirmation) {
            if (!confirm("There are unsaved changes which will be lost.")) {
                return Promise.reject(new Error('Navigation cancelled by user'));
            }
            this.confirmation = undefined;
        }
        */
        // Show location in window manager
        const Galaxy = getGalaxyInstance();
        if (title && Galaxy.frame && Galaxy.frame.active) {
            Galaxy.frame.add({ title, url: location });
            return Promise.resolve();
        }

        // Always emit event, even when a duplicate route is pushed
        // this.app.emit("router-push");

        // Avoid console warning when user clicks to revisit the same route
        return originalPush.call(this, location).catch((err: Error) => {
            if (err.name !== "NavigationDuplicated") {
                throw err;
            }
        });
    };
}
