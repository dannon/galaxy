define(['axios'], function (__module__0) {
    'use strict';
    var exports = {};
    var module = { exports: {} };
    Object.defineProperty(exports, '__esModule', { value: true });
    var _axios = __module__0;
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
    (typeof module.exports === 'function' ? module.exports.options : module.exports).template = '<div id=form-userkeys class=toolForm v-cloak=""><div class=toolFormTitle>User Information</div><div v-if="users &amp;&amp; users.length > 0"><table class=grid><thead><tr><th>Encoded UID<th>Email<th>API Key<th>Actions<tbody><tr v-for="user in users"><td>{{ user.uid }}<td>{{ user.email }}<td>{{ user.key }}<td><input type=button value="Generate a new key now" v-on:click="generateKey( user.uid )"></table></div><div v-else=""><div>No user information available</div></div><div style="clear: both"></div></div>';
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