/**
 * jQuery and all its horrible plugins. Bundled together and repackaged into a
 * single module, then aliased by webpack as "jquery"
 */

var jQuery = require("jqueryVendor");

require("imports-loader?jQuery=jqueryVendor!@bower_components/jquery-autocomplete/src/jquery.autocomplete.js");
require("imports-loader?jQuery=jqueryVendor!jquery.event.drag");
require("imports-loader?jQuery=jqueryVendor!jquery.event.drop");
require("imports-loader?jQuery=jqueryVendor,define=>false!jquery-mousewheel");
require("imports-loader?jQuery=jqueryVendor!jquery-form");
require("imports-loader?jQuery=jqueryVendor!jquery-rating");
require("imports-loader?jQuery=jqueryVendor!select2");
require("imports-loader?jQuery=jqueryVendor!jquery-ui");
require("imports-loader?jQuery=jqueryVendor!@bower_components/farbtastic");
require("imports-loader?jQuery=jqueryVendor,$=jqueryVendor,define=>false!jquery.cookie");
require("imports-loader?jQuery=jqueryVendor!@bower_components/dynatree/dist/jquery.dynatree");
require("imports-loader?jQuery=jqueryVendor!jquery.complexify");
require("imports-loader?jQuery=jqueryVendor!jquery-migrate");

module.exports = jQuery;
