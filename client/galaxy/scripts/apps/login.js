/* global jQuery, $, _, Backbone */
import { setGalaxyInstance } from "galaxy";
import _l from "utils/localization";
import Page from "layout/page";


function initLoginEndpoint(options, bootstrapped) {
    
    let { options, bootstrapped } = rawConfig;

    let Galaxy = setGalaxyInstance(GalaxyApp => {
        let newApp = new GalaxyApp(options, bootstrapped);
        newApp.debug("login app");
        return newApp;
    })

    var redirect = encodeURI(options.redirect);

    // TODO: remove iframe for user login (at least) and render login page from here
    // then remove this redirect
    if (!options.show_welcome_with_login) {
        var params = jQuery.param({ use_panels: "True", redirect: redirect });
        window.location.href = `${Galaxy.root}user/login?${params}`;
        return;
    }

    var LoginPage = Backbone.View.extend({
        initialize: function(page) {
            this.page = page;
            this.model = new Backbone.Model({ title: _l("Login required") });
            this.setElement(this._template());
        },
        render: function() {
            this.page.$("#galaxy_main").prop("src", options.welcome_url);
        },
        _template: function() {
            var login_url = `${options.root}user/login?${$.param({
                redirect: redirect
            })}`;
            return `<iframe src="${login_url}" frameborder="0" style="width: 100%; height: 100%;"/>`;
        }
    });

    $(() => {
        Galaxy.page = new Page.View(
            _.extend(options, {
                Right: LoginPage
            })
        );
    });
};

function launch() {

    console.group("Initialize login endpoint");
    loadConfigs()
        .then(config => {

            // initialize raven early
            initializeRaven(config);

            // fire up main app
            let app = initLoginEndpoint(config);

            // misc loading scripts that shouldn't exist
            onloadHandler();
  
            console.log("Login endpoint initialized", app);
            console.groupEnd();
        })
        .catch(err => {
            console.log("Unable to initialize login.js", err);
            console.groupEnd();
        });

}


window.addEventListener('load', launch);