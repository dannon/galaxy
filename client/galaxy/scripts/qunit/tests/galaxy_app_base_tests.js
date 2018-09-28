/* global define */
import { setGalaxyInstance, getGalaxyInstance, resetGalaxyInstance } from "galaxy";
import options from "../test-data/bootstrapped";

// reset singleton after each test
QUnit.module("Galaxy client app tests", {
    afterEach: function() {
        resetGalaxyInstance();
    }
});

QUnit.test("App base construction/initializiation defaults", function(assert) {
    var app = setGalaxyInstance(GalaxyApp => {
        return new GalaxyApp({});
    });

    assert.ok(app.hasOwnProperty("options") && typeof app.options === "object");
    assert.ok(app.hasOwnProperty("logger") && typeof app.logger === "object");
    assert.ok(app.hasOwnProperty("localize") && typeof app.localize === "function");
    assert.ok(app.hasOwnProperty("config") && typeof app.config === "object");
    assert.ok(app.hasOwnProperty("user") && typeof app.config === "object");
    assert.equal(app.localize, window._l);
});

QUnit.test("App base default options", function(assert) {
    var app = setGalaxyInstance(GalaxyApp => {
        return new GalaxyApp({});
    });

    assert.ok(app.hasOwnProperty("options") && typeof app.options === "object");
    assert.equal(app.options.root, "/");
    assert.equal(app.options.patchExisting, true);
});

QUnit.test("App base extends from Backbone.Events", function(assert) {
    var app = setGalaxyInstance(GalaxyApp => {
        return new GalaxyApp({});
    });
    ["on", "off", "trigger", "listenTo", "stopListening"].forEach(function(fn) {
        assert.ok(app.hasOwnProperty(fn) && typeof app[fn] === "function");
    });
});

QUnit.test("App base has logging methods from utils/add-logging.js", function(assert) {
    var app = setGalaxyInstance(GalaxyApp => {
        return new GalaxyApp({});
    });
    ["debug", "info", "warn", "error", "metric"].forEach(function(fn) {
        assert.ok(typeof app[fn] === "function");
    });
    assert.ok(app._logNamespace === "GalaxyApp");
});

QUnit.test("App base logger", function(assert) {
    var app = setGalaxyInstance(GalaxyApp => {
        return new GalaxyApp({});
    });
    assert.ok(app.hasOwnProperty("logger") && typeof app.config === "object");
});

QUnit.test("App base config", function(assert) {
    var app = setGalaxyInstance(GalaxyApp => {
        return new GalaxyApp(options);
    });
    assert.ok(app.hasOwnProperty("config") && typeof app.config === "object");
    assert.equal(app.config.allow_user_deletion, false);
    assert.equal(app.config.allow_user_creation, true);
    assert.equal(app.config.wiki_url, "https://galaxyproject.org/");
    assert.equal(app.config.ftp_upload_site, null);
});

QUnit.test("App base user", function(assert) {
    var app = setGalaxyInstance(GalaxyApp => {
        return new GalaxyApp({});
    });
    assert.ok(app.hasOwnProperty("user") && typeof app.user === "object");
    assert.ok(app.user.isAdmin() === false);
});

QUnit.test("App singleton can't be set twice", assert => {
    setGalaxyInstance(GalaxyApp => {
        return new GalaxyApp({});
    });
    assert.throws(() => {
        setGalaxyInstance(GalaxyApp => {
            return new GalaxyApp({});
        });
    }, Error);
});
