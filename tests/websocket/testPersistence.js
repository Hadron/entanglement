"use strict";
var entanglement = require('../../javascript');
var assert = require('assert');
let sr = new entanglement.SyncRegistry();
var websocket_schemas = require("./schemas/websocket_test");
websocket_schemas(sr);
var sql_meta_schema = require('./schemas/sql_meta');
sql_meta_schema(sr);
var persistence = require('../../javascript/persistence');
persistence.setupPersistence(sr);

var permit_success = false;

class TestPhase extends sr.bases.TestPhase(persistence.PersistentSynchronizable) {

    async     syncReceive(msg, options) {
        await super.syncReceive(msg, options);
        if (options.operation == 'sync') {
            console.log(`Received phase ${this.phase}`);
            switch(this.phase) {
            case 1:
                assert.equal(this.syncOwner.id, this._sync_owner);
                
                    
                let update = await this.syncClone();
                update.phase = 2;
                let res1 = await update.syncUpdate(sm);
                assert.strictEqual(this, res1);
                break;
            case 2:
                await testCreates(this._sync_owner);
                this.phase = 3;
                this.syncUpdate(sm);
                break;
            case 3:
                // We need to wait for syncReceive to finish so our event handlers fire and update the relation
                // The phase 1 syncreceive doesn't finish until after the phase 2
                if (this.syncOwner.testPhases.indexOf(this) == -1) {
                    console.log(`test phases: ${this.syncOwner.testPhases}`);
                    throw new ApplicationError("This not in testPhases");
                    }
                
                let trans_obj = await testUsTransitioning(this._sync_owner);
                await testBreakingTransition(this._sync_owner, this, trans_obj);
                // Phase 5 causes this object to disappear.
                break;
            case 4:
                setTimeout(() => {
                    assert.equal(permit_success, true);
                    this.phase = 5;
                    this.syncUpdate(sm);
                }, 150);
                
                break;
            }
        }
    }
}

persistence.relationship(TestPhase,persistence.SyncOwner,
                         {
                             keys: '_sync_owner',
                             debug: true
                         });

TestPhase.addEventListener( 'disappear', (obj, msg) => {
    assert.equal(permit_success, true);
    process.exit(0);
});

class TableInherits extends sr.bases.TableInherits(persistence.PersistentSynchronizable) {
}
class TableTransition extends sr.bases.TableTransition(TableInherits) { }

sr.register(TableInherits);

sr.register(TestPhase);
sr.register(TableTransition);
            var sm = new entanglement.SyncManager({
    url: process.argv[2],
    registries: [sr],
});


async function testCreates(owner) {
    let  ti = new TableInherits();
    ti._sync_owner = owner;
    ti.info = "initial";
    let result = await ti.syncCreate(sm);
    if (!(result instanceof TableInherits)) {
        throw new TypeError(`${result} is not a TableInherits`);
    }
    assert.equal(ti.info, result.info);
    result.info2 = "barbaz";
    let result2 = await result.syncUpdate(sm);
    assert.equal(result, result2);
    await testDelete(result);
}

async function testDelete(result) {
    let res2 = await result.syncDelete(sm);
    assert.equal(result, res2);
    let res3= result.constructor.syncConstruct(res2.toSync({}), {});
    if (!(res3 instanceof  res2.constructor))
        throw new TypeError("Unexpected class");
    if ( res2 === res3)
        throw new TypeError ("Unexpectedly same objects after delete");
}

async function testUsTransitioning(owner) {
    let obj = new TableTransition();
    obj.info = 40;
    obj._sync_owner = owner;
    obj = await obj.syncCreate(sm);
    obj.info2 = 90;
    console.log("Starting Transition");
    let transition_promise = sm.perform_transition(obj);
    transition_promise.catch((e) => console.error(e));
    if (!(obj.transition_id && obj.transition_promise && obj._orig_pre_transition))
        throw new TypeError("transition_id expected");
    obj.syncUpdate(sm).catch( (e) => console.error(e));
    await transition_promise;
    assert.equal(obj.info2, 90);
    if (obj.transition_id || obj._orig_pre_transition)
        throw new TypeError("Transition cleanup failed");
    return obj;
}

async function testBreakingTransition(owner, phase, trans_obj) {
    // Now that we have an object, bring it back into transition, have
    // the other side break the transition, and confirm our changes
    // revert
    trans_obj.info2 = "wrong";
    let promise = sm.perform_transition(trans_obj);
    // phase 4 will cause the server to find our object and re-synchronize it.
    console.log(trans_obj._orig_pre_transition);
    phase.phase = 4;
    promise.then((r) => {
        console.error("Transition successful");
        process.exit(3);
    }, (r) => {
        assert.equal(r._sync_type, 'BrokenTransition');
        setTimeout(() => {
            assert.equal(trans_obj.info2, 90);
            permit_success = true;
        }, 9);
        
    });
    await phase.syncUpdate(sm);
}

setTimeout(() => process.exit(2), 10000);
