import msa from "msa";

Object.assign( window.bundleEntries || {}, {
	load: function (e) {
		var t = e.chart,
			r = e.dataset,
			s = t.settings,
			n = new msa.msa({
				el: $("#" + e.targets[0]),
				vis: { conserv: "true" == s.get("conserv"), overviewbox: "true" == s.get("overviewbox") },
				menu: "small",
				bootstrapMenu: "true" == s.get("menu"),
			});
		n.u.file.importURL(r.download_url, function () {
			n.render(), t.state("ok", "Chart drawn."), e.process.resolve();
		});
	},
});
