/*
loadConfigs returns raw session/configuration data for the main application via
a promise. Right now we'll nab it from the page itself where the python spits
out the data in json blocks in server-rendered scrip tags. Later this will
likely be an ajax call to an api endpoint or a retrieval from a cached version
in local storage (or both)
*/

const defaultConfigs = {
    options: {}, 
    bootstrapped: {}, 
    root: "/",
    raven: {
        use_raven: false,
        sentry_dsn_public: null,
        user_email: null
    }
};

let configs = Object.assign({}, defaultConfigs);

/**
 * Explicitly load configs. Even though this simply reads the config off the
 * python-rendered window variable this will probably be an ajax call
 * eventually, so return as a promise for now.
 */
export function loadConfigs() {
    console.log("loadConfigs");
    Object.assign(configs, window.galaxyConfig);
    return Promise.resolve(configs);
}


/**
 * getAppRoot(), returns configured application root address
 *
 * This should probably be a promise as well. But the only files using Galaxy
 * root on primitive property values are Backbone objects that are probably
 * going to be replaced anyway. If we end up keeping these backbone objects then
 * we should implement a controlled definition pipeline 
 *
 * e.g. 
 *       loadConfigs().then(config => {
 *          // define models in context of the
 *          // application config
 *       });
 */
export function getAppRoot() {
    return configs.root;
}
