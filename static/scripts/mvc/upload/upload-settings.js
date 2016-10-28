define(["utils/utils"],function(a){return Backbone.View.extend({options:{class_check:"fa-check-square-o",class_uncheck:"fa-square-o",parameters:[{id:"space_to_tab",title:"Convert spaces to tabs"},{id:"to_posix_lines",title:"Use POSIX standard"}]},initialize:function(a){this.model=a.model,this.setElement($("<div/>").addClass("upload-settings")),this.$el.append($("<div/>").addClass("upload-settings-cover")),this.$el.append($("<table/>").addClass("upload-settings-table ui-table-striped").append("<tbody/>")),this.$cover=this.$(".upload-settings-cover"),this.$table=this.$(".upload-settings-table > tbody"),this.listenTo(this.model,"change",this.render,this),this.model.trigger("change")},render:function(){var a=this;this.$table.empty(),_.each(this.options.parameters,function(b){var c=$("<div/>").addClass("upload-"+b.id+" upload-icon-button fa").addClass(a.model.get(b.id)&&a.options.class_check||a.options.class_uncheck).on("click",function(){a.model.get("enabled")&&a.model.set(b.id,!a.model.get(b.id))});a.$table.append($("<tr/>").append($("<td/>").append(c)).append($("<td/>").append(b.title)))}),this.$cover[this.model.get("enabled")&&"hide"||"show"]()}})});
//# sourceMappingURL=../../../maps/mvc/upload/upload-settings.js.map