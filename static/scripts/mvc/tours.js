define(["libs/bootstrap-tour"],function(a){var b="undefined"==typeof Galaxy?"/":Galaxy.root,c={storage:window.sessionStorage,onEnd:function(){sessionStorage.removeItem("activeGalaxyTour")},delay:150,orphan:!0},d=function(a){return _.each(a.steps,function(a){a.preclick&&(a.onShow=function(){_.each(a.preclick,function(a){$(a).click()})}),a.postclick&&(a.onHide=function(){_.each(a.postclick,function(a){$(a).click()})}),a.textinsert&&(a.onShown=function(){$(a.element).val(a.textinsert).trigger("change")})}),a},e=Backbone.Model.extend({urlRoot:b+"api/tours"}),f=Backbone.Collection.extend({url:b+"api/tours",model:e}),g=function(a){var e=b+"api/tours/"+a;$.getJSON(e,function(a){var b=d(a);sessionStorage.setItem("activeGalaxyTour",JSON.stringify(a));var e=new Tour(_.extend({steps:b.steps},c));e.init(),e.goTo(0),e.restart()})},h=Backbone.View.extend({initialize:function(){var a=this;this.setElement("<div/>"),this.model=new f,this.model.fetch({success:function(){a.render()},error:function(){console.error("Failed to fetch tours.")}})},render:function(){var a=_.template(["<h2>Galaxy Tours</h2>","<p>This page presents a list of interactive tours available on this Galaxy server.  ","Select any tour to get started (and remember, you can click 'End Tour' at any time).</p>","<ul>","<% _.each(tours, function(tour) { %>","<li>",'<a href="/tours/<%- tour.id %>" class="tourItem" data-tour.id=<%- tour.id %>>',"<%- tour.attributes.name || tour.id %>","</a>",' - <%- tour.attributes.description || "No description given." %>',"</li>","<% }); %>","</ul>"].join(""));this.$el.html(a({tours:this.model.models})).on("click",".tourItem",function(a){a.preventDefault(),g($(this).data("tour.id"))})}});return{ToursView:h,hooked_tour_from_data:d,tour_opts:c,giveTour:g}});
//# sourceMappingURL=../../maps/mvc/tours.js.map