define(["utils/utils","utils/deferred","mvc/ui/ui-misc","mvc/form/form-view","mvc/form/form-data","mvc/tool/tool-form-base","mvc/ui/ui-modal","mvc/webhooks"],function(a,b,c,d,e,f,g,h){var i=Backbone.View.extend({initialize:function(a){var c=this;this.modal=parent.Galaxy.modal||new g.View,this.model=a&&a.model||new Backbone.Model(a),this.deferred=new b,this.setElement($("<div/>").addClass("ui-form-composite").append(this.$message=$("<div/>")).append(this.$header=$("<div/>")).append(this.$steps=$("<div/>"))),$("body").append(this.$el),this._configure(),this.render(),this._refresh(),this.$el.on("click",function(){c._refresh()}),this.$steps.scroll(function(){c._refresh()}),$(window).resize(function(){c._refresh()})},_refresh:function(a){var b=_.reduce(this.$el.children(),function(a,b){return a+$(b).outerHeight()},0)-this.$steps.height()+25;this.$steps.css("height",$(window).height()-b)},_configure:function(){function b(a,b){var e=c._isWorkflowParameter(a);e&&b(c.wp_inputs[e]=c.wp_inputs[e]||{label:e,name:e,type:"text",color:"hsl( "+100*++d+", 70%, 30% )",style:"ui-form-wp-source",links:[]})}var c=this;this.forms=[],this.steps=[],this.links=[],this.parms=[],_.each(this.model.get("steps"),function(b,d){Galaxy.emit.debug("tool-form-composite::initialize()",d+" : Preparing workflow step."),b=a.merge({index:d,name:"Step "+(parseInt(d)+1)+": "+b.name,icon:"",help:null,description:b.annotation&&" - "+b.annotation||b.description,citations:null,collapsible:!0,collapsed:d>0&&!c._isDataStep(b),sustain_version:!0,sustain_repeats:!0,sustain_conditionals:!0,narrow:!0,text_enable:"Edit",text_disable:"Undo",cls_enable:"fa fa-edit",cls_disable:"fa fa-undo",errors:b.messages,initial_errors:!0,cls:"ui-portlet-narrow",hide_operations:!0,needs_refresh:!1,always_refresh:"tool"!=b.step_type},b),c.steps[d]=b,c.links[d]=[],c.parms[d]={}}),_.each(this.steps,function(a,b){e.visitInputs(a.inputs,function(a,d){c.parms[b][d]=a})}),_.each(this.steps,function(a,b){_.each(a.output_connections,function(a){_.each(c.steps,function(d,e){d.step_index===a.input_step_index&&c.links[b].push(d)})})}),_.each(this.steps,function(a,b){_.each(c.steps,function(d,e){var f={};_.each(a.output_connections,function(a){d.step_index===a.input_step_index&&(f[a.input_name]=a)}),_.each(c.parms[e],function(c,d){var e=f[d];e&&(c.type="hidden",c.help=c.step_linked?c.help+", ":"",c.help+="Output dataset '"+e.output_name+"' from step "+(parseInt(b)+1),c.step_linked=c.step_linked||[],c.step_linked.push(a))})})});var d=0;this.wp_inputs={},_.each(this.steps,function(a,d){_.each(c.parms[d],function(c,d){b(c.value,function(b){b.links.push(a),c.wp_linked=b.name,c.color=b.color,c.type="text",c.value=null,c.backdrop=!0,c.style="ui-form-wp-target"})}),_.each(a.post_job_actions,function(a){_.each(a.action_arguments,function(a){b(a,function(){})})})}),_.each(this.steps,function(b,d){if("tool"==b.step_type){var f=!0;e.visitInputs(b.inputs,function(d,e,g){var h=["data","data_collection"].indexOf(d.type)!=-1,i=g[d.data_ref];d.step_linked&&!c._isDataStep(d.step_linked)&&(f=!1),d.options&&(0==d.options.length&&!f||d.wp_linked)&&(d.is_workflow=!0),i&&(d.is_workflow=i.step_linked&&!c._isDataStep(i.step_linked)||d.wp_linked),(h||d.value&&"RuntimeValue"==d.value.__class__&&!d.step_linked)&&(b.collapsed=!1),d.value&&"RuntimeValue"==d.value.__class__&&(d.value=null),d.flavor="workflow",h||"hidden"===d.type||d.wp_linked||(d.optional||!a.isEmpty(d.value)&&""!==d.value)&&(d.collapsible_value=d.value,d.collapsible_preview=!0)})}})},render:function(){var a=this;this.deferred.reset(),this._renderHeader(),this._renderMessage(),this._renderParameters(),this._renderHistory(),_.each(this.steps,function(b){a._renderStep(b)})},_renderHeader:function(){var a=this;this.execute_btn=new c.Button({icon:"fa-check",title:"Run workflow",cls:"btn btn-primary",onclick:function(){a._execute()}}),this.$header.addClass("ui-form-header").empty().append(new c.Label({title:"Workflow: "+this.model.get("name")}).$el).append(this.execute_btn.$el)},_renderMessage:function(){this.$message.empty(),this.model.get("has_upgrade_messages")&&this.$message.append(new c.Message({message:"Some tools in this workflow may have changed since it was last saved or some errors were found. The workflow may still run, but any new options will have default values. Please review the messages below to make a decision about whether the changes will affect your analysis.",status:"warning",persistent:!0,fade:!1}).$el);var a=this.model.get("step_version_changes");a&&a.length>0&&this.$message.append(new c.Message({message:"Some tools are being executed with different versions compared to those available when this workflow was last saved because the other versions are not or no longer available on this Galaxy instance. To upgrade your workflow and dismiss this message simply edit the workflow and re-save it.",status:"warning",persistent:!0,fade:!1}).$el)},_renderParameters:function(){var a=this;this.wp_form=null,_.isEmpty(this.wp_inputs)||(this.wp_form=new d({title:"<b>Workflow Parameters</b>",inputs:this.wp_inputs,cls:"ui-portlet-narrow",onchange:function(){_.each(a.wp_form.input_list,function(b,c){_.each(b.links,function(b){a._refreshStep(b)})})}}),this._append(this.$steps.empty(),this.wp_form.$el))},_renderHistory:function(){this.history_form=new d({cls:"ui-portlet-narrow",title:"<b>History Options</b>",inputs:[{type:"conditional",name:"new_history",test_param:{name:"check",label:"Send results to a new history",type:"boolean",value:"false",help:""},cases:[{value:"true",inputs:[{name:"name",label:"History name",type:"text",value:this.model.get("name")}]}]}]}),this._append(this.$steps,this.history_form.$el)},_renderStep:function(b){var c=this,e=null;this.deferred.execute(function(g){c.$steps.addClass("ui-steps"),"tool"==b.step_type?(e=new f(b),b.post_job_actions&&b.post_job_actions.length&&e.portlet.append($("<div/>").addClass("ui-form-element-disabled").append($("<div/>").addClass("ui-form-title").html("<b>Job Post Actions</b>")).append($("<div/>").addClass("ui-form-preview").html(_.reduce(b.post_job_actions,function(a,b){return a+" "+b.short_str},""))))):(_.each(b.inputs,function(a){a.flavor="module"}),e=new d(a.merge({title:"<b>"+b.name+"</b>",onchange:function(){_.each(c.links[b.index],function(a){c._refreshStep(a)})},inputs:b.inputs&&b.inputs.length>0?b.inputs:[{type:"hidden",name:"No options available.",ignore:null}]},b))),c.forms[b.index]=e,c._append(c.$steps,e.$el),b.needs_refresh&&c._refreshStep(b),e.portlet[c.show_progress?"disable":"enable"](),c.show_progress&&c.execute_btn.model.set({wait:!0,wait_text:"Preparing...",percentage:100*(b.index+1)/c.steps.length}),Galaxy.emit.debug("tool-form-composite::initialize()",b.index+" : Workflow step state ready.",b),setTimeout(function(){g.resolve()},0)})},_refreshStep:function(a){var b=this,c=this.forms[a.index];c?(_.each(b.parms[a.index],function(a,d){if(a.step_linked||a.wp_linked){var e=c.field_list[c.data.match(d)];if(e){var f=void 0;if(a.step_linked)f={values:[]},_.each(a.step_linked,function(a){b._isDataStep(a)&&(value=b.forms[a.index].data.create().input,value&&_.each(value.values,function(a){f.values.push(a)}))}),!a.multiple&&f.values.length>0&&(f={values:[f.values[0]]});else if(a.wp_linked){var g=b.wp_form.field_list[b.wp_form.data.match(a.wp_linked)];g&&(f=g.value())}void 0!==f&&e.value(f)}}}),c.trigger("change")):a.needs_refresh=!0},_refreshHistory:function(){var a=this,b=parent.Galaxy&&parent.Galaxy.currHistoryPanel&&parent.Galaxy.currHistoryPanel.model;this._refresh_history&&clearTimeout(this._refresh_history),b&&b.refresh().success(function(){0===b.numOfUnfinishedShownContents()&&(a._refresh_history=setTimeout(function(){a._refreshHistory()},b.UPDATE_DELAY))})},_execute:function(){var a=this;this.show_progress=!0,this._enabled(!1),this.deferred.execute(function(b){setTimeout(function(){b.resolve(),a._submit()},0)})},_submit:function(){var b=this,c=this.history_form.data.create(),d={new_history_name:c["new_history|name"]?c["new_history|name"]:null,history_id:c["new_history|name"]?null:this.model.get("history_id"),replacement_params:this.wp_form?this.wp_form.data.create():{},parameters:{},parameters_normalized:!0,batch:!0},e=!0;for(var f in this.forms){var g=this.forms[f],i=g.data.create(),j=b.steps[f],k=j.step_index;g.trigger("reset");for(var l in i){var m=i[l],n=g.data.match(l),o=(g.field_list[n],g.input_list[n]);if(!o.step_linked){if(e=this._isDataStep(j)?m&&m.values&&m.values.length>0:o.optional||o.is_workflow&&""!==m||!o.is_workflow&&null!==m,!e){g.highlight(n);break}d.parameters[k]=d.parameters[k]||{},d.parameters[k][l]=i[l]}}if(!e)break}e?(Galaxy.emit.debug("tool-form-composite::submit()","Validation complete.",d),a.request({type:"POST",url:Galaxy.root+"api/workflows/"+this.model.id+"/invocations",data:d,success:function(a){if(Galaxy.emit.debug("tool-form-composite::submit","Submission successful.",a),b.$el.children().hide(),b.$el.append(b._templateSuccess(a)),$.isArray(a)&&a.length>0){b.$el.append($("<div/>",{id:"webhook-view"}));new h.WebhookView({urlRoot:Galaxy.root+"api/webhooks/workflow"})}b._refreshHistory()},error:function(a){Galaxy.emit.debug("tool-form-composite::submit","Submission failed.",a);var c=!1;if(a&&a.err_data)for(var e in b.forms){var f=b.forms[e],g=a.err_data[f.options.step_index];if(g){var h=f.data.matchResponse(g);for(var i in h){f.highlight(i,h[i]),c=!0;break}}}c||b.modal.show({title:"Workflow submission failed",body:b._templateError(d,a&&a.err_msg),buttons:{Close:function(){b.modal.hide()}}})},complete:function(){b._enabled(!0)}})):(b._enabled(!0),Galaxy.emit.debug("tool-form-composite::submit()","Validation failed.",d))},_append:function(a,b){a.append("<p/>").append(b)},_enabled:function(a){this.execute_btn.model.set({wait:!a,wait_text:"Sending...",percentage:-1}),this.wp_form&&this.wp_form.portlet[a?"enable":"disable"](),this.history_form&&this.history_form.portlet[a?"enable":"disable"](),_.each(this.forms,function(b){b&&b.portlet[a?"enable":"disable"]()})},_isWorkflowParameter:function(b){if("$"===String(b).substring(0,1))return a.sanitize(b.substring(2,b.length-1))},_isDataStep:function(a){lst=$.isArray(a)?a:[a];for(var b=0;b<lst.length;b++){var c=lst[b];if(!c||!c.step_type||!c.step_type.startsWith("data"))return!1}return!0},_templateSuccess:function(b){return $.isArray(b)&&b.length>0?$("<div/>").addClass("donemessagelarge").append($("<p/>").html("Successfully invoked workflow <b>"+a.sanitize(this.model.get("name"))+"</b>"+(b.length>1?" <b>"+b.length+" times</b>":"")+".")).append($("<p/>").append("<b/>").text("You can check the status of queued jobs and view the resulting data by refreshing the History pane. When the job has been run the status will change from 'running' to 'finished' if completed successfully or 'error' if problems were encountered.")):this._templateError(b,"Invalid success response. No invocations found.")},_templateError:function(a,b){return $("<div/>").addClass("errormessagelarge").append($("<p/>").text("The server could not complete the request. Please contact the Galaxy Team if this error persists. "+(JSON.stringify(b)||""))).append($("<pre/>").text(JSON.stringify(a,null,4)))}});return{View:i}});
//# sourceMappingURL=../../../maps/mvc/tool/tool-form-composite.js.map