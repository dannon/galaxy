import Raven from "raven";

export function initializeRaven(configs) {
    console.log("Initializing Raven");

    let r = configs.raven;

    if (r.use_raven && r.sentry_dsn_public) {
        Raven.config(r.sentry_dsn_public).install();
        if (r.user_email) {
            Raven.setUser({ email: r.user_email });
        }
    }
}
