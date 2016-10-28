define([],function(){return Backbone.View.extend({initialize:function(a,b){this.app=a,this.app_options=a.options||{},this.field=b&&b.field||new Backbone.View,this.model=b&&b.model||new Backbone.Model({text_enable:this.app_options.text_enable||"Enable",text_disable:this.app_options.text_disable||"Disable",cls_enable:this.app_options.cls_enable||"fa fa-caret-square-o-down",cls_disable:this.app_options.cls_disable||"fa fa-caret-square-o-up"}).set(b),this.setElement(this._template()),this.$field=this.$(".ui-form-field"),this.$info=this.$(".ui-form-info"),this.$preview=this.$(".ui-form-preview"),this.$collapsible=this.$(".ui-form-collapsible"),this.$collapsible_text=this.$(".ui-form-collapsible-text"),this.$collapsible_icon=this.$(".ui-form-collapsible-icon"),this.$title=this.$(".ui-form-title"),this.$title_text=this.$(".ui-form-title-text"),this.$error_text=this.$(".ui-form-error-text"),this.$error=this.$(".ui-form-error"),this.$backdrop=this.$(".ui-form-backdrop"),this.$field.prepend(this.field.$el);var c=this.model.get("collapsible_value");this.field.collapsed=void 0!==c&&JSON.stringify(this.model.get("value"))==JSON.stringify(c),this.listenTo(this.model,"change",this.render,this),this.render();var d=this;this.$collapsible.on("click",function(){d.field.collapsed=!d.field.collapsed,a.trigger&&a.trigger("change"),d.render()})},backdrop:function(){this.model.set("backdrop",!0)},error:function(a){this.model.set("error_text",a)},reset:function(){this.model.set("error_text",null)},render:function(){$(".tooltip").hide();var a=this.model.get("help",""),b=this.model.get("argument");b&&a.indexOf("("+b+")")==-1&&(a+=" ("+b+")"),this.$info.html(a),this.$el[this.model.get("hidden")?"hide":"show"](),this.$preview[this.field.collapsed&&this.model.get("collapsible_preview")||this.model.get("disabled")?"show":"hide"]().html(_.escape(this.model.get("text_value")));var c=this.model.get("error_text");if(this.$error[c?"show":"hide"](),this.$el[c?"addClass":"removeClass"]("ui-error"),this.$error_text.html(c),this.$backdrop[this.model.get("backdrop")?"show":"hide"](),this.field.collapsed||this.model.get("disabled")?this.$field.hide():this.$field.show(),this.field.model&&this.field.model.set({color:this.model.get("color"),style:this.model.get("style")}),this.model.get("disabled")||void 0===this.model.get("collapsible_value"))this.$title_text.show().text(this.model.get("label")),this.$collapsible.hide();else{var d=this.field.collapsed?"enable":"disable";this.$title_text.hide(),this.$collapsible.show(),this.$collapsible_text.text(this.model.get("label")),this.$collapsible_icon.removeClass().addClass("icon").addClass(this.model.get("cls_"+d)).attr("data-original-title",this.model.get("text_"+d)).tooltip({placement:"bottom"})}},_template:function(){return $("<div/>").addClass("ui-form-element").append($("<div/>").addClass("ui-form-error ui-error").append($("<span/>").addClass("fa fa-arrow-down")).append($("<span/>").addClass("ui-form-error-text"))).append($("<div/>").addClass("ui-form-title").append($("<div/>").addClass("ui-form-collapsible").append($("<i/>").addClass("ui-form-collapsible-icon")).append($("<span/>").addClass("ui-form-collapsible-text"))).append($("<span/>").addClass("ui-form-title-text"))).append($("<div/>").addClass("ui-form-field").append($("<span/>").addClass("ui-form-info")).append($("<div/>").addClass("ui-form-backdrop"))).append($("<div/>").addClass("ui-form-preview"))}})});
//# sourceMappingURL=../../../maps/mvc/form/form-input.js.map