const colors = {
	Approved: "green",
	Requested: "blue",
	Cancelled: "red",
	Rejected: "red",
};

frappe.listview_settings["Work From Home"] = {
	get_indicator(doc) {
		// customize indicator color
		if (colors[doc.status]) {
			return [__(`${doc.status}`), colors[doc.status], `status,=,${doc.status}`];
		} else {
			return [__("else"), "grey", "status,=,else"];
		}
	},
};
