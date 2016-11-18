define(["utils/utils","mvc/tool/tool-form-base"],function(a,b){var c=Backbone.View.extend({initialize:function(c){var d=this;this.workflow=c.workflow,this.node=c.node,this.setElement("<div/>"),this.node?(this.post_job_actions=this.node.post_job_actions||{},a.deepeach(c.inputs,function(b){b.type&&(["data","data_collection"].indexOf(b.type)!=-1?(b.type="hidden",b.info="Data input '"+b.name+"' ("+a.textify(b.extensions)+")",b.value={__class__:"RuntimeValue"}):(b.collapsible_value={__class__:"RuntimeValue"},b.is_workflow=b.options&&0==b.options.length||["integer","float"].indexOf(b.type)!=-1))}),a.deepeach(c.inputs,function(a){"conditional"==a.type&&(a.test_param.collapsible_value=void 0)}),this._makeSections(c),this.form=new b(a.merge(c,{text_enable:"Set in Advance",text_disable:"Set at Runtime",narrow:!0,initial_errors:!0,sustain_version:!0,cls:"ui-portlet-narrow",update_url:Galaxy.root+"api/workflows/build_module",update:function(a){d.node.update_field_data(a),d.form.errors(a&&a.tool_model)}})),this.$el.append(this.form.$el)):Galaxy.emit.debug("tool-form-workflow::initialize()","Node not found in workflow.")},_makeSections:function(b){var c=b.inputs,d=b.datatypes;c[a.uid()]={label:"Annotation / Notes",name:"annotation",type:"text",area:!0,help:"Add an annotation or note for this step. It will be shown with the workflow.",value:this.node.annotation};var e=this.node.output_terminals&&Object.keys(this.node.output_terminals)[0];if(e){c[a.uid()]={name:"pja__"+e+"__EmailAction",label:"Email notification",type:"boolean",value:String(Boolean(this.post_job_actions["EmailAction"+e])),ignore:"false",help:"An email notification will be sent when the job has completed.",payload:{host:window.location.host}},c[a.uid()]={name:"pja__"+e+"__DeleteIntermediatesAction",label:"Output cleanup",type:"boolean",value:String(Boolean(this.post_job_actions["DeleteIntermediatesAction"+e])),ignore:"false",help:"Upon completion of this step, delete non-starred outputs from completed workflow steps if they are no longer required as inputs."};for(var f in this.node.output_terminals)c[a.uid()]=this._makeSection(f,d)}},_makeSection:function(a,b){function c(b,e){e=e||[],e.push(b);for(var f in b.inputs){var g=b.inputs[f],h=g.action;if(h){if(g.name="pja__"+a+"__"+g.action,g.pja_arg&&(g.name+="__"+g.pja_arg),g.payload)for(var i in g.payload){var j=g.payload[i];g.payload[g.name+"__"+i]=j,delete j}var k=d.post_job_actions[g.action+a];if(k){for(var l in e)e[l].expanded=!0;g.pja_arg?g.value=k.action_arguments&&k.action_arguments[g.pja_arg]||g.value:g.value="true"}}g.inputs&&c(g,e.slice(0))}}var d=this,e=[],f=[];for(key in b)e.push({0:b[key],1:b[key]});for(key in this.node.input_terminals)f.push(this.node.input_terminals[key].name);e.sort(function(a,b){return a.label>b.label?1:a.label<b.label?-1:0}),e.unshift({0:"Sequences",1:"Sequences"}),e.unshift({0:"Roadmaps",1:"Roadmaps"}),e.unshift({0:"Leave unchanged",1:"__empty__"});var g={title:"Configure Output: '"+a+"'",type:"section",flat:!0,inputs:[{label:"Label",type:"text",value:(output=this.node.getWorkflowOutput(a))&&output.label||"",help:"This will provide a short name to describe the output - this must be unique across workflows.",onchange:function(b){d.workflow.attemptUpdateOutputLabel(d.node,a,b)}},{action:"RenameDatasetAction",pja_arg:"newname",label:"Rename dataset",type:"text",value:"",ignore:"",help:'This action will rename the output dataset. Click <a href="https://wiki.galaxyproject.org/Learn/AdvancedWorkflow/Variables">here</a> for more information. Valid inputs are: <strong>'+f.join(", ")+"</strong>."},{action:"ChangeDatatypeAction",pja_arg:"newtype",label:"Change datatype",type:"select",ignore:"__empty__",value:"__empty__",options:e,help:"This action will change the datatype of the output to the indicated value."},{action:"TagDatasetAction",pja_arg:"tags",label:"Tags",type:"text",value:"",ignore:"",help:"This action will set tags for the dataset."},{title:"Assign columns",type:"section",flat:!0,inputs:[{action:"ColumnSetAction",pja_arg:"chromCol",label:"Chrom column",type:"integer",value:"",ignore:""},{action:"ColumnSetAction",pja_arg:"startCol",label:"Start column",type:"integer",value:"",ignore:""},{action:"ColumnSetAction",pja_arg:"endCol",label:"End column",type:"integer",value:"",ignore:""},{action:"ColumnSetAction",pja_arg:"strandCol",label:"Strand column",type:"integer",value:"",ignore:""},{action:"ColumnSetAction",pja_arg:"nameCol",label:"Name column",type:"integer",value:"",ignore:""}],help:"This action will set column assignments in the output dataset. Blank fields are ignored."}]};return c(g),g}});return{View:c}});
//# sourceMappingURL=../../../maps/mvc/tool/tool-form-workflow.js.map