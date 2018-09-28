/** Creates a generic/global Galaxy environment, loads shared libraries and a fake server */
/* global define */

define([
    "jquery",
    "sinon",
    "bootstrap",
    "backbone",
    "qunit/test-data/bootstrapped",
    "qunit/test-data/fakeserver",
    "libs/jquery/select2",
    "libs/jquery/jstorage",
    "libs/jquery/jquery-ui"
], function($, sinon, bootstrap, Backbone, bootstrapped, serverdata) {
    // sample config
    window.galaxyConfig = {
        boostrapped: bootstrapped
    };

    $("head").append(
        $('<link rel="stylesheet" type="text/css"  />').attr("href", "/base/galaxy/scripts/qunit/assets/base.css")
    );

    return {
        create: function() {
            window.fakeserver = sinon.fakeServer.create();
            for (var route in serverdata) {
                window.fakeserver.respondWith("GET", window.Galaxy.root + route, [
                    200,
                    { "Content-Type": "application/json" },
                    serverdata[route].data
                ]);
            }
        },
        destroy: function() {
            if (window.fakeserver) {
                window.fakeserver.restore();
                delete window.fakeserver;
            }
        }
    };
});
