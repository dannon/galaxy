define([
    'vueify/lib/insert-css',
    'axios',
    'libs/bibtexParse',
    'latex-to-unicode-converter',
    'latex-parser'
], function (__module__0, __module__1, __module__2, __module__3, __module__4) {
    'use strict';
    var exports = {};
    var module = { exports: {} };
    var __vueify_insert__ = __module__0;
    var __vueify_style__ = __vueify_insert__.insert('.citations-formatted{word-wrap:break-word}.citations-bibtex-text{width:100%;height:500px}.citation-padding{padding:5px 10px}');
    Object.defineProperty(exports, '__esModule', { value: true });
    var _axios = __module__1;
    var _axios2 = _interopRequireDefault(_axios);
    var _bibtexParse = __module__2;
    var bibtexParse = _interopRequireWildcard(_bibtexParse);
    var _latexToUnicodeConverter = __module__3;
    var _latexParser = __module__4;
    function _interopRequireWildcard(obj) {
        if (obj && obj.__esModule) {
            return obj;
        } else {
            var newObj = {};
            if (obj != null) {
                for (var key in obj) {
                    if (Object.prototype.hasOwnProperty.call(obj, key))
                        newObj[key] = obj[key];
                }
            }
            newObj.default = obj;
            return newObj;
        }
    }
    function _interopRequireDefault(obj) {
        return obj && obj.__esModule ? obj : { default: obj };
    }
    exports.default = {
        props: {
            source: {
                type: String,
                required: true
            },
            id: {
                type: String,
                required: true
            },
            viewRender: {
                type: Boolean,
                requried: false,
                default: true
            }
        },
        data: function data() {
            return {
                citations: [],
                content: '',
                errors: []
            };
        },
        computed: {
            formattedReferences: function formattedReferences() {
                var _this = this;
                return this.citations.reduce(function (a, b) {
                    return a.concat('<p>' + _this.formattedReference(b) + '</p>');
                }, '');
            }
        },
        created: function created() {
            var _this2 = this;
            _axios2.default.get(Galaxy.root + 'api/' + this.source + '/' + this.id + '/citations').then(function (response) {
                _this2.content = '';
                var _iteratorNormalCompletion = true;
                var _didIteratorError = false;
                var _iteratorError = undefined;
                try {
                    for (var _iterator = response.data[Symbol.iterator](), _step; !(_iteratorNormalCompletion = (_step = _iterator.next()).done); _iteratorNormalCompletion = true) {
                        var rawCitation = _step.value;
                        try {
                            var citation = {
                                fields: {},
                                entryType: undefined
                            };
                            var parsed = bibtexParse.toJSON(rawCitation.content);
                            if (parsed) {
                                parsed = _.first(parsed);
                                citation.entryType = parsed.entryType || undefined;
                                for (var key in parsed.entryTags) {
                                    citation.fields[key.toLowerCase()] = parsed.entryTags[key];
                                }
                            }
                            _this2.citations.push(citation);
                            _this2.content += rawCitation.content;
                        } catch (err) {
                            console.warn('Error parsing bibtex: ' + err);
                        }
                    }
                } catch (err) {
                    _didIteratorError = true;
                    _iteratorError = err;
                } finally {
                    try {
                        if (!_iteratorNormalCompletion && _iterator.return) {
                            _iterator.return();
                        }
                    } finally {
                        if (_didIteratorError) {
                            throw _iteratorError;
                        }
                    }
                }
            }).catch(function (e) {
                console.error(e);
            });
        },
        methods: {
            formattedReference: function formattedReference(citation) {
                var entryType = citation.entryType;
                var fields = citation.fields;
                var ref = '';
                var authorsAndYear = this._asSentence((fields.author ? fields.author : '') + (fields.year ? ' (' + fields.year + ')' : '')) + ' ';
                var title = fields.title || '';
                var pages = fields.pages ? 'pp. ' + fields.pages : '';
                var address = fields.address;
                if (entryType == 'article') {
                    var volume = (fields.volume ? fields.volume : '') + (fields.number ? ' (' + fields.number + ')' : '') + (pages ? ', ' + pages : '');
                    ref = authorsAndYear + this._asSentence(title) + (fields.journal ? 'In <em>' + fields.journal + ', ' : '') + this._asSentence(volume) + this._asSentence(fields.address) + '</em>';
                } else if (entryType == 'inproceedings' || entryType == 'proceedings') {
                    ref = authorsAndYear + this._asSentence(title) + (fields.booktitle ? 'In <em>' + fields.booktitle + ', ' : '') + (pages ? pages : '') + (address ? ', ' + address : '') + '.</em>';
                } else if (entryType == 'mastersthesis' || entryType == 'phdthesis') {
                    ref = authorsAndYear + this._asSentence(title) + (fields.howpublished ? fields.howpublished + '. ' : '') + (fields.note ? fields.note + '.' : '');
                } else if (entryType == 'techreport') {
                    ref = authorsAndYear + this._asSentence(title) + this._asSentence(fields.institution) + this._asSentence(fields.number) + this._asSentence(fields.type);
                } else if (entryType == 'book' || entryType == 'inbook' || entryType == 'incollection') {
                    ref = authorsAndYear + ' ' + this._formatBookInfo(fields);
                } else {
                    ref = authorsAndYear + ' ' + this._asSentence(title) + this._asSentence(fields.howpublished) + this._asSentence(fields.note);
                }
                var doiUrl = '';
                if (fields.doi) {
                    doiUrl = 'http://dx.doi.org/' + fields.doi;
                    ref += '[<a href="' + doiUrl + '" target="_blank">doi:' + fields.doi + '</a>]';
                }
                var url = fields.url || doiUrl;
                if (url) {
                    ref += '[<a href="' + url + '" target="_blank">Link</a>]';
                }
                return (0, _latexToUnicodeConverter.convertLaTeX)({
                    onError: function onError(error, latex) {
                        return '{' + (0, _latexParser.stringifyLaTeX)(latex) + '}';
                    }
                }, ref);
            },
            _formatBookInfo: function _formatBookInfo(fields) {
                var info = '';
                if (fields.chapter) {
                    info += fields.chapter + ' in ';
                }
                if (fields.title) {
                    info += '<em>' + fields.title + '</em>';
                }
                if (fields.editor) {
                    info += ', Edited by ' + fields.editor + ', ';
                }
                if (fields.publisher) {
                    info += ', ' + fields.publisher;
                }
                if (fields.pages) {
                    info += ', pp. ' + fields.pages;
                }
                if (fields.series) {
                    info += ', <em>' + fields.series + '</em>';
                }
                if (fields.volume) {
                    info += ', Vol.' + fields.volume;
                }
                if (fields.issn) {
                    info += ', ISBN: ' + fields.issn;
                }
                return info + '.';
            },
            _asSentence: function _asSentence(str) {
                return str && str.trim() ? str + '. ' : '';
            },
            toggleViewRender: function toggleViewRender() {
                this.viewRender = !this.viewRender;
            }
        }
    };
    if (module.exports.__esModule)
        module.exports = module.exports.default;
    (typeof module.exports === 'function' ? module.exports.options : module.exports).template = '<div class=toolForm><div class=toolFormTitle>Citations <button v-if=viewRender v-on:click=toggleViewRender type=button class="btn btn-xs" title="Show all in BibTeX format."><i class="fa fa-pencil-square-o"></i> Show BibTeX</button> <button v-else="" type=button v-on:click=toggleViewRender class="btn btn-xs" title="Return to formatted citation list."><i class="fa fa-times"></i> Hide BibTeX</button></div><div class="toolFormBody citationPadding"><div v-if="source === \'histories\'" class=citation-padding><b>Warning: This is a experimental feature.</b> Most Galaxy tools will not annotate citations explicitly at this time. When writing up your analysis, please manually review your histories and find all references that should be cited in order to completely describe your work. Also, please remember to <a href=https://galaxyproject.org/citing-galaxy>cite Galaxy</a>.</div></div><div class="citations-bibtex toolFormBody citation-padding"><span v-if=viewRender class=citations-formatted><p v-html=formattedReferences></span><textarea v-else="" class=citations-bibtex-text>                {{ content }}\n        </textarea></div></div>';
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