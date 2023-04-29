"use strict";

import * as entanglement from '../../javascript/index.js';

var sm = new entanglement.SyncManager(process.argv[2]);

function first_receive(m) {
    sm.remove_on_receive('TableTransition', first_receive);
    var trans_obj = {id: m.id,
		     _sync_owner: m._sync_owner,
		 info2: 10,
		 _sync_type: m._sync_type
		};
    var attributes = Object.keys(trans_obj);
    var promise = sm.perform_transition(trans_obj, attributes);
    for (var i = 0; i <= 10; i++) {
	trans_obj.info2 = i;
	sm.perform_transition(trans_obj, attributes);
    }
    sm.synchronize(trans_obj, attributes, 'forward', true);
    promise.then( success => {
	if (success === null) {process.exit(3);}
	if (success._sync_type != 'TableTransition') {process.exit(1);}
	if (success.info2 != trans_obj.info2) {process.exit(1); }
	process.exit(0);
    },
		  failure => process.exit(2));
    
}
sm.on_receive('TableTransition', first_receive);
