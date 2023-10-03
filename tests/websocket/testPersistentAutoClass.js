"use strict";
var entanglement = require('../../javascript');
var persistence = require('../../javascript/persistence');
var assert = require('assert');
let sr = new entanglement.SyncRegistry({base:persistence.PersistentSynchronizable});

var websocket_schemas = require("./schemas/websocket_test");
websocket_schemas(sr);
var sql_meta_schema = require('./schemas/sql_meta');
sql_meta_schema(sr);
persistence.setupPersistence(sr);

            var sm = new entanglement.SyncManager({
    url: process.argv[2],
    registries: [sr],
});
let TableInherits = sr.registry.get('TableInherits');
TableInherits.addEventListener('sync', (e) => {process.exit(0)})
setTimeout(() => process.exit(2), 10000);
