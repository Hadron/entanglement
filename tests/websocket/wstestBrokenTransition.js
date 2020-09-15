"use strict";
var entanglement = require('../../javascript/entanglement');
var sm = new entanglement.SyncManager(process.argv[2]);

var test_obj = {'_sync_type': 'TableTransition',
	    info: "33",
	    '_sync_owner': process.argv[3]
	   };
sm.onopen( event => {
    var res = sm.synchronize(test_obj, Object.keys(test_obj), 'create', true);
    res.then(on_create_response, m => {
        console.error(m);
        process.exit(1);
    });
});

function prep_transition(t) {
    return {
	id: t.id,
	_sync_type: t._sync_type,
	info2: t.info2,
	_sync_owner: t._sync_owner
    };
}

function on_create_response(m) {
    console.log("In on_create_response");
    m.info2 = 1;
    var m2 = prep_transition(m);
    var res = sm.perform_transition(m2, Object.keys(m2));
    m.info2 = 3;
    m2 = prep_transition(m);
    setTimeout(() => {
	sm.perform_transition(m2, Object.keys(m2));
	res.then( m => process.exit(2),
		  m => {
		      if (m._sync_type != 'BrokenTransition') {
			  process.exit(3);
		      }
		      process.exit(0);
		  });
    }, 250);
    }
