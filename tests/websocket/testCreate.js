"use strict";
var entanglement = require('../../javascript/entanglement.js');
var sm = new entanglement.SyncManager(process.argv[2]);
var test_obj = {'_sync_type': 'TableInherits',
	    info: "33",
	    '_sync_owner': process.argv[3]
	   };
sm.onopen( event => {
    var res = sm.synchronize(test_obj, Object.keys(test_obj), 'create', true);


    res.then(m => {
	console.log("Got Response!");
	process.exit(0);
    });

});
