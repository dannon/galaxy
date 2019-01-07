define("mvc/upload/collection/collection-row",["exports","utils/localization","utils/utils","mvc/upload/upload-model","mvc/upload/upload-settings","mvc/ui/ui-popover","mvc/ui/ui-select"],function(t,e,s,i,o,a,l){"use strict";function n(t){return t&&t.__esModule?t:{default:t}}Object.defineProperty(t,"__esModule",{value:!0});var d=n(e),h=n(s),r=(n(i),n(o)),c=n(a);n(l);t.default=Backbone.View.extend({status_classes:{init:"upload-icon-button fa fa-trash-o",queued:"upload-icon fa fa-spinner fa-spin",running:"upload-icon fa fa-spinner fa-spin",success:"upload-icon-button fa fa-check",error:"upload-icon-button fa fa-exclamation-triangle"},initialize:function(t,e){var s=this;this.app=t,this.model=e.model,this.setElement(this._template(e.model)),this.$mode=this.$(".upload-mode"),this.$title=this.$(".upload-title-extended"),this.$text=this.$(".upload-text"),this.$size=this.$(".upload-size"),this.$info_text=this.$(".upload-info-text"),this.$info_progress=this.$(".upload-info-progress"),this.$text_content=this.$(".upload-text-content"),this.$symbol=this.$(".upload-symbol"),this.$progress_bar=this.$(".upload-progress-bar"),this.$percentage=this.$(".upload-percentage"),this.settings=new c.default.View({title:(0,d.default)("Upload configuration"),container:this.$(".upload-settings"),placement:"bottom"});this.app.select_genome.value(),this.app.select_extension.value();this.$symbol.on("click",function(){s._removeRow()}),this.$text_content.on("change input",function(t){s.model.set({url_paste:$(t.target).val(),file_size:$(t.target).val().length})}),this.listenTo(this.model,"change:percentage",function(){s._refreshPercentage()}),this.listenTo(this.model,"change:status",function(){s._refreshStatus()}),this.listenTo(this.model,"change:info",function(){s._refreshInfo()}),this.listenTo(this.model,"change:file_size",function(){s._refreshFileSize()}),this.listenTo(this.model,"remove",function(){s.remove()}),this.app.collection.on("reset",function(){s.remove()})},render:function(){var t=this.model.attributes;this.$title.html(_.escape(t.file_name)),this.$size.html(h.default.bytesToString(t.file_size)),this.$mode.removeClass().addClass("upload-mode").addClass("text-primary"),"new"==t.file_mode?(this.$text.css({width:this.$el.width()-16+"px",top:this.$el.height()-8+"px"}).show(),this.$el.height(this.$el.height()-8+this.$text.height()+16),this.$mode.addClass("fa fa-edit")):"local"==t.file_mode?this.$mode.addClass("fa fa-laptop"):"ftp"==t.file_mode&&this.$mode.addClass("fa fa-folder-open-o")},_refreshInfo:function(){var t=this.model.get("info");t?this.$info_text.html("<strong>Failed: </strong>"+t).show():this.$info_text.hide()},_refreshPercentage:function(){var t=parseInt(this.model.get("percentage"));this.$progress_bar.css({width:t+"%"}),this.$percentage.html(100!=t?t+"%":"Adding to history...")},_refreshStatus:function(){var t=this.model.get("status");this.$symbol.removeClass().addClass("upload-symbol").addClass(this.status_classes[t]),this.model.set("enabled","init"==t);var e=this.model.get("enabled");this.$text_content.attr("disabled",!e),"success"==t&&(this.$el.addClass("table-success"),this.$percentage.html("100%")),"error"==t&&(this.$el.addClass("table-danger"),this.$info_progress.hide())},_refreshFileSize:function(){this.$size.html(h.default.bytesToString(this.model.get("file_size")))},_removeRow:function(){-1!==["init","success","error"].indexOf(this.model.get("status"))&&this.app.collection.remove(this.model)},_showSettings:function(){this.settings.visible?this.settings.hide():(this.settings.empty(),this.settings.append(new r.default(this).$el),this.settings.show())},_template:function(t){return'<tr id="upload-row-'+t.id+'" class="upload-row"><td><div class="upload-text-column"><div class="upload-mode"/><div class="upload-title-extended"/><div class="upload-text"><div class="upload-text-info">You can tell Galaxy to download data from web by entering URL in this box (one per line). You can also directly paste the contents of a file.</div><textarea class="upload-text-content form-control"/></div></div></td><td><div class="upload-size"/></td><td><div class="upload-info"><div class="upload-info-text"/><div class="upload-info-progress progress"><div class="upload-progress-bar progress-bar progress-bar-success"/><div class="upload-percentage">0%</div></div></div></td><td><div class="upload-symbol '+this.status_classes.init+'"/></td></tr>'}})});
//# sourceMappingURL=../../../../maps/mvc/upload/collection/collection-row.js.map