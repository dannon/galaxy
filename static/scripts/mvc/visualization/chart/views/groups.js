define("mvc/visualization/chart/views/groups",["exports","utils/utils","mvc/ui/ui-misc","mvc/form/form-view","mvc/form/form-repeat","mvc/form/form-data","mvc/visualization/chart/views/description"],function(e,t,i,a,s,n,r){"use strict";function o(e){return e&&e.__esModule?e:{default:e}}Object.defineProperty(e,"__esModule",{value:!0});var d=o(t),u=o(i),l=o(a),h=o(s),c=o(n),p=o(r),f=Backbone.View.extend({initialize:function(e,t){var i=this;this.deferred=e.deferred,this.chart=e.chart,this.group=t.group,this.setElement($("<div/>")),this.listenTo(this.chart,"change:dataset_id",function(){i.render()}),this.render()},render:function(){var e=this,t=d.default.clone(this.chart.plugin.groups)||{},i=this.chart.get("dataset_id");i&&(this.chart.state("wait","Loading metadata..."),this.deferred.execute(function(a){d.default.get({url:Galaxy.root+"api/datasets/"+i,cache:!0,success:function(i){var s={};c.default.visitInputs(t,function(t,a){if("data_column"==t.type){s[a]=d.default.clone(t);var n=[];t.is_auto&&n.push({label:"Column: Row Number",value:"auto"}),t.is_zero&&n.push({label:"Column: None",value:"zero"});var r=i.metadata_column_types;for(var o in r)(-1!=["int","float"].indexOf(r[o])&&t.is_numeric||t.is_label)&&n.push({label:"Column: "+(parseInt(o)+1),value:o});t.data=n}var u=e.group.get(a);void 0!==u&&!t.hidden&&(t.value=u)}),t.__data_columns={name:"__data_columns",type:"hidden",hidden:!0,value:s},e.chart.state("ok","Metadata initialized..."),e.form=new l.default({inputs:t,cls:"ui-portlet-plain",onchange:function(){e.group.set(e.form.data.create()),e.chart.set("modified",!0)}}),e.group.set(e.form.data.create()),e.$el.empty().append(e.form.$el),a.resolve()}})}))}});e.default=Backbone.View.extend({initialize:function(e){var t=this;this.app=e,this.chart=e.chart,this.repeat=new h.default.View({title:"Data series",title_new:"Data series",min:1,onnew:function(){t.chart.groups.add({id:d.default.uid()})}}),this.description=new p.default(this.app),this.message=new u.default.Message({message:"There are no data selection options for this visualization type.",persistent:!0,status:"info"}),this.setElement($("<div/>").append(this.description.$el).append(this.repeat.$el.addClass("ui-margin-bottom")).append(this.message.$el.addClass("ui-margin-bottom"))),this.listenTo(this.chart,"change",function(){t.render()}),this.listenTo(this.chart.groups,"add remove reset",function(){t.chart.set("modified",!0)}),this.listenTo(this.chart.groups,"remove",function(e){t.repeat.del(e.id)}),this.listenTo(this.chart.groups,"reset",function(){t.repeat.delAll()}),this.listenTo(this.chart.groups,"add",function(e){t.repeat.add({id:e.id,cls:"ui-portlet-panel",$el:new f(t.app,{group:e}).$el,ondel:function(){t.chart.groups.remove(e)}})})},render:function(){_.size(this.chart.plugin.groups)>0?(this.repeat.$el.show(),this.message.$el.hide()):(this.repeat.$el.hide(),this.message.$el.show())}})});