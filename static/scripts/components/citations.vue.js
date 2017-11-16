var __vueify_insert__ = require("vueify/lib/insert-css")
var __vueify_style__ = __vueify_insert__.insert("\n.citations-formatted{\n    word-wrap: break-word;\n}\n\n.citations-bibtex-text{\n    width: 100%;\n    height: 500px;\n}\n\n.citation-padding{\n    padding:5px 10px;\n}\n")
"use strict";

Object.defineProperty(exports, "__esModule", {
    value: true
});

var _axios = require("axios");

var _axios2 = _interopRequireDefault(_axios);

var _bibtexParse = require("libs/bibtexParse");

var bibtexParse = _interopRequireWildcard(_bibtexParse);

var _latexToUnicodeConverter = require("latex-to-unicode-converter");

var _latexParser = require("latex-parser");

function _interopRequireWildcard(obj) { if (obj && obj.__esModule) { return obj; } else { var newObj = {}; if (obj != null) { for (var key in obj) { if (Object.prototype.hasOwnProperty.call(obj, key)) newObj[key] = obj[key]; } } newObj.default = obj; return newObj; } }

function _interopRequireDefault(obj) { return obj && obj.__esModule ? obj : { default: obj }; }

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
            content: "",
            errors: []
        };
    },

    computed: {
        formattedReferences: function formattedReferences() {
            var _this = this;

            return this.citations.reduce(function (a, b) {
                return a.concat("<p>" + _this.formattedReference(b) + "</p>");
            }, "");
        }
    },
    created: function created() {
        var _this2 = this;

        _axios2.default.get(Galaxy.root + "api/" + this.source + "/" + this.id + "/citations").then(function (response) {
            _this2.content = "";
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
                        console.warn("Error parsing bibtex: " + err);
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

            var ref = "";
            var authorsAndYear = this._asSentence((fields.author ? fields.author : "") + (fields.year ? " (" + fields.year + ")" : "")) + " ";
            var title = fields.title || "";
            var pages = fields.pages ? "pp. " + fields.pages : "";
            var address = fields.address;
            if (entryType == "article") {
                var volume = (fields.volume ? fields.volume : "") + (fields.number ? " (" + fields.number + ")" : "") + (pages ? ", " + pages : "");
                ref = authorsAndYear + this._asSentence(title) + (fields.journal ? "In <em>" + fields.journal + ", " : "") + this._asSentence(volume) + this._asSentence(fields.address) + "</em>";
            } else if (entryType == "inproceedings" || entryType == "proceedings") {
                ref = authorsAndYear + this._asSentence(title) + (fields.booktitle ? "In <em>" + fields.booktitle + ", " : "") + (pages ? pages : "") + (address ? ", " + address : "") + ".</em>";
            } else if (entryType == "mastersthesis" || entryType == "phdthesis") {
                ref = authorsAndYear + this._asSentence(title) + (fields.howpublished ? fields.howpublished + ". " : "") + (fields.note ? fields.note + "." : "");
            } else if (entryType == "techreport") {
                ref = authorsAndYear + this._asSentence(title) + this._asSentence(fields.institution) + this._asSentence(fields.number) + this._asSentence(fields.type);
            } else if (entryType == "book" || entryType == "inbook" || entryType == "incollection") {
                ref = authorsAndYear + " " + this._formatBookInfo(fields);
            } else {
                ref = authorsAndYear + " " + this._asSentence(title) + this._asSentence(fields.howpublished) + this._asSentence(fields.note);
            }
            var doiUrl = "";
            if (fields.doi) {
                doiUrl = "http://dx.doi.org/" + fields.doi;
                ref += "[<a href=\"" + doiUrl + "\" target=\"_blank\">doi:" + fields.doi + "</a>]";
            }
            var url = fields.url || doiUrl;
            if (url) {
                ref += "[<a href=\"" + url + "\" target=\"_blank\">Link</a>]";
            }
            return (0, _latexToUnicodeConverter.convertLaTeX)({ onError: function onError(error, latex) {
                    return "{" + (0, _latexParser.stringifyLaTeX)(latex) + "}";
                } }, ref);
        },
        _formatBookInfo: function _formatBookInfo(fields) {
            var info = "";
            if (fields.chapter) {
                info += fields.chapter + " in ";
            }
            if (fields.title) {
                info += "<em>" + fields.title + "</em>";
            }
            if (fields.editor) {
                info += ", Edited by " + fields.editor + ", ";
            }
            if (fields.publisher) {
                info += ", " + fields.publisher;
            }
            if (fields.pages) {
                info += ", pp. " + fields.pages;
            }
            if (fields.series) {
                info += ", <em>" + fields.series + "</em>";
            }
            if (fields.volume) {
                info += ", Vol." + fields.volume;
            }
            if (fields.issn) {
                info += ", ISBN: " + fields.issn;
            }
            return info + ".";
        },
        _asSentence: function _asSentence(str) {
            return str && str.trim() ? str + ". " : "";
        },
        toggleViewRender: function toggleViewRender() {
            this.viewRender = !this.viewRender;
        }
    }
};
if (module.exports.__esModule) module.exports = module.exports.default
;(typeof module.exports === "function"? module.exports.options: module.exports).template = "\n<div class=\"toolForm\">\n    <div class=\"toolFormTitle\">\n        Citations\n        <button v-if=\"viewRender\" v-on:click=\"toggleViewRender\" type=\"button\" class=\"btn btn-xs\" title=\"Show all in BibTeX format.\">\n            <i class=\"fa fa-pencil-square-o\"></i>\n            Show BibTeX\n        </button>\n        <button v-else=\"\" type=\"button\" v-on:click=\"toggleViewRender\" class=\"btn btn-xs\" title=\"Return to formatted citation list.\">\n            <i class=\"fa fa-times\"></i>\n            Hide BibTeX\n        </button>\n    </div>\n    <div class=\"toolFormBody citationPadding\">\n        <div v-if=\"source === 'histories'\" class=\"citation-padding\">\n            <b>Warning: This is a experimental feature.</b> Most Galaxy\n            tools will not annotate citations explicitly at this time. When\n            writing up your analysis, please manually review your histories\n            and find all references that should be cited in order to\n            completely describe your work. Also, please remember to <a href=\"https://galaxyproject.org/citing-galaxy\">cite Galaxy</a>.\n        </div>\n    </div>\n    <div class=\"citations-bibtex toolFormBody citation-padding\">\n        <span v-if=\"viewRender\" class=\"citations-formatted\">\n            <p v-html=\"formattedReferences\">\n            </p>\n        </span>\n        <textarea v-else=\"\" class=\"citations-bibtex-text\">                {{ content }}\n        </textarea>\n    </div>\n</div>\n"
if (module.hot) {(function () {  module.hot.accept()
  var hotAPI = require("vue-hot-reload-api")
  hotAPI.install(require("vue"), true)
  if (!hotAPI.compatible) return
  module.hot.dispose(function () {
    __vueify_insert__.cache["\n.citations-formatted{\n    word-wrap: break-word;\n}\n\n.citations-bibtex-text{\n    width: 100%;\n    height: 500px;\n}\n\n.citation-padding{\n    padding:5px 10px;\n}\n"] = false
    document.head.removeChild(__vueify_style__)
  })
  if (!module.hot.data) {
    hotAPI.createRecord("_v-3a8d5b3a", module.exports)
  } else {
    hotAPI.update("_v-3a8d5b3a", module.exports, (typeof module.exports === "function" ? module.exports.options : module.exports).template)
  }
})()}