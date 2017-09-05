"use strict";
var entanglement = require('../../javascript/entanglement.js');
var sm = new entanglement.SyncManager(process.argv[2]);

sm.on_receive('TableTransition', o => {
    process.exit(0);
});
