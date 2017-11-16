define([
    'vueify/lib/insert-css',
    'axios',
    'vue-hot-reload-api',
    'vue'
], function (__module__0, __module__1, __module__2, __module__3) {
    'use strict';
    var exports = {};
    var module = { exports: {} };
    var __vueify_insert__ = __module__0;
    var __vueify_style__ = __vueify_insert__.insert('\n');
    Object.defineProperty(exports, '__esModule', { value: true });
    var _axios = __module__1;
    var _axios2 = _interopRequireDefault(_axios);
    function _interopRequireDefault(obj) {
        return obj && obj.__esModule ? obj : { default: obj };
    }
    exports.default = {
        data: function data() {
            return {
                users: [],
                errors: []
            };
        },
        created: function created() {
            var _this = this;
            _axios2.default.get(Galaxy.root + 'userskeys/all_users').then(function (response) {
                _this.users = response.data;
            }).catch(function (e) {
                _this.errors.push(e);
            });
        },
        methods: {
            generateKey: function generateKey(id) {
                var _this2 = this;
                _axios2.default.get(Galaxy.root + 'userskeys/admin_api_keys', { params: { uid: id } }).then(function (response) {
                    _this2.users = response.data;
                }).catch(function (e) {
                    _this2.errors.push(e);
                });
            }
        }
    };
    if (module.exports.__esModule)
        module.exports = module.exports.default;
    (typeof module.exports === 'function' ? module.exports.options : module.exports).template = '\n<div id="form-userkeys" class="toolForm" v-cloak="">\n    <div class="toolFormTitle">User Information</div>\n    <div v-if="users &amp;&amp; users.length > 0">\n        <table class="grid">\n            <thead><tr><th>Encoded UID</th><th>Email</th><th>API Key</th><th>Actions</th></tr></thead>\n            <tbody>\n                <tr v-for="user in users">\n                    <td>\n                        {{ user.uid }}\n                    </td>\n                    <td>\n                        {{ user.email }}\n                    </td>\n                    <td>\n                        {{ user.key }}\n                    </td>\n                    <td>\n                        <input type="button" value="Generate a new key now" v-on:click="generateKey( user.uid )">\n                    </td>\n                </tr>\n            </tbody>\n        </table>\n    </div>\n    <div v-else="">\n        <div>No user information available</div>\n    </div>\n    <div style="clear: both"></div>\n</div>\n';
    if (module.hot) {
        (function () {
            module.hot.accept();
            var hotAPI = __module__2;
            hotAPI.install(__module__3, true);
            if (!hotAPI.compatible)
                return;
            module.hot.dispose(function () {
                __vueify_insert__.cache['\n'] = false;
                document.head.removeChild(__vueify_style__);
            });
            if (!module.hot.data) {
                hotAPI.createRecord('_v-4f9416e4', module.exports);
            } else {
                hotAPI.update('_v-4f9416e4', module.exports, (typeof module.exports === 'function' ? module.exports.options : module.exports).template);
            }
        }());
    }
    function __isEmptyObject(obj) {
        var attr;
        for (attr in obj)
            return !1;
        return !0;
    }
    function __isValidToReturn(obj) {
        return typeof obj != 'object' || Array.isArray(obj) || !__isEmptyObject(obj);
    }
    if (__isValidToReturn(module.exports))
        return module.exports;
    else if (__isValidToReturn(exports))
        return exports;
});