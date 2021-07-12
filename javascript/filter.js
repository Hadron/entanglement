"use strict";
/*
 * Copyright (C) 2017, 2020, Hadron Industries, Inc.
 *  Entanglement is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU Lesser General Public License version 3
 *  as published by the Free Software Foundation. It is distributed
 *  WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
 *  LICENSE for details.
*/

const util = require('./util');

const default_delete_ops = Object.freeze(new Set([
     'delete',
    'disappear',
]));
const default_add_ops = Object.freeze(new Set([
    'sync'
]));

class FilterBase {
    constructor( options) {
        // Much of the logic is encapsulated here to provide
        // encapsulation and to make things easier for the compiler by
        // not letting references escape.
        if (! (options.target && options.filter&& options.add && options.remove)) 
            throw new TypeError("Add, remove, filter, and target are required");


        const self = this; // Don't want to bind so we get event handlers that can be removed
        const add = options.add;
        const remove = options.remove;
        const filter = options.filter;
        if (!options.debug_name)
            options.debug_name = `Filter on ${options.target.name}`;
        const debug = options.debug || false;

        const filter_category_map = new WeakMap();

        function addOperationHandler(obj) {
            let old_category = filter_category_map.get(obj);
            let new_category = filter(obj);
            if (debug)
                console.log(`Filter ${options.debug_name} old_category: ${old_category} new_category: ${new_category}`);
            self.onObjectChange(obj);
            // We want to treat null, undefined and false the same.
            if (new_category != old_category) {
                self.onChange(obj, old_category, new_category);
                if (old_category) {
                    self.onRemove(obj, old_category);
                    remove(obj, old_category);
                }
                if (new_category) {
                    self.onAdd(obj, new_category);
                    add(obj, new_category);
                    filter_category_map.set(obj, new_category);
                }            else filter_category_map.delete(obj);
            }
        }

        function deleteOperationHandler(obj) {
            let old_category = filter_category_map.get(obj);
            self.onObjectChange(obj);
            if (old_category) {
                self.onChange(obj, old_category, undefined);
                self.onRemove(obj);
                remove(obj, old_category);
                filter_category_map.delete(obj);
            }
        }

        let add_ops = new Set(default_add_ops);
        let delete_ops = new Set(default_delete_ops);
        if (options.includeTransitions) {
            add_ops.add('transition');
        }
        this.add_ops = Object.freeze(add_ops);
        this.delete_ops = Object.freeze(delete_ops);

        // before adding listeners let's go add anything already in the collection
        if (options.collection)
            for (let o of options.collection) {
                if (! (o instanceof target))
                    continue;
                addOperationHandler(o);
            }
        
        for (let o of add_ops)
            options.target.addEventListener(o, addOperationHandler);
        for (let o of delete_ops)
            options.target.addEventListener(o, deleteOperationHandler);

        this.target = options.target;
        this._addOperationHandler = addOperationHandler;
        this._deleteOperationHandler = deleteOperationHandler;
        
    }

    close() {
        for (let o of this.add_ops) 
            this.target.removeEventListener(o, this._addOperationHandler);
        for (let o of this.delete_ops)
            this.target.removeEventListener(o, this._deleteOperationHandler);
        this.target = null;
    }

    onObjectChange(obj) { }

    onChange(obj, old_cat, new_cat) {
            //Called on any change of filter membership
    }

    onAdd(obj, filter_cat) { }

    onRemove(obj, filter_cat) { }

};

function removeList(l, obj) {
    let idx = l.indexOf(obj);
    if (idx != -1)
        l.splice(idx, 1);
}

function addList(l, obj) {
    if (l.indexOf(obj) == -1) {
        l.push(obj);
        l.sort_required = true;
    }
}

function getList(l, order) {
    if (l.sort_required && order) {
        l.sort(order);
        l.sort_required = false;
    }
    return l;
}

function filter(options) {
    // Filter returning a result that is a list
    const filter_result = [];
    filter_result.sort_required = false;
    const filter = new FilterBase(
        { 
         add: (obj) => addList(filter_result, obj),
            remove: (obj) => removeList(filter_result, obj),
            ...options
        });
    Object.defineProperty(
        filter, 'result',
        {get: () => getList(filter_result, options.order),
         enumerable: true});
    return filter;
}

function mapFilter(options) {
    const filter_result = (options.map)?(new options.map):new Map;
    const empty_node = options.empty_node || (() => []);
    const addItem = options.add_item || addList;
    const deleteItem = options.delete_item ||removeList;
    const getItem = options.get_item || ((l) => getList(l, options.order));
    const filter = new FilterBase({
        ...options,
        add: (obj, key) => {
            if (!filter_result.has(key))
                filter_result.set(key, empty_node(key));
            const node = filter_result.get(key);
            addItem(node, obj);
        },
        remove: (obj, key) => {
            const node = filter_result.get(key);
            if (node)
                deleteItem(node, obj);
        },
    });
    Object.assign(
        filter,
        {
            get(k)  {
                let res = filter_result.get(k);
                if (res !== undefined)
                    return getItem(res);
                // This might need revisiting as there is no resource
                // cleanup, but a lot of code expects empty lists
                // rather than undefined.  One possibility would be to
                // return a new empty list all the time, but in
                // environments like vue 2 where that might be mutated
                // to observe, that would be problematic.  For direct
                // use of mapFilter we have the onAdd method etc.  But
                // for relationships it is more complex.
                res = empty_node(k);
                filter_result.set(k, res);
                return res;
            },
            has(k) {return filter_result.has(k)},
        });
    
    return filter;
}

/**
   * Indicate a relationship between two :class:`Synchronizables`.
   * The *local* class is the one that in a database would have a
   * foreign key constraint; the one with a higher sync_priority.
   * This supports one-to-one and one-to-many relationships.
   *
   * :param local: The local class
   * :param options: A set of options for the relationship:
   *
   * use_list
   *    Defaults to true; if true, then the remote side of the relationship is a list (one-to-many).  If "object" then an object is used and "object_key" contains the key in the local object to use to populate the object stored in remote_prop in the remote.
   *
   * remote
   *    Class that is the other side of the relationship
   *
   * keys
   *    An array of key names mapping to the local columns that hold the remote's :meth:`yncPrimaryKeys`
   *
   * local_prop
   *    The The local name of a property to use; defaults to downcased remote name.
   *
   * remote_prop
   *    Same for remote.  Defaults to downcased local name possibly with 's' added.
   */
function relationship(local, remote, options) {
    if (!options.keys)
        throw new TypeError("keys option is required");
    if(!Array.isArray(options.keys))
        options.keys = [options.keys];
    const keys = Object.freeze(options.keys);
        if (options.use_list === undefined)
            options.use_list = true;
    const use_list = options.use_list;
    const debug = options.debug || false;
    const missing_node = options.missing_node || null;
    const local_prop = options.local_prop ||util.downFirst(remote.name);
    let remote_prop;
    if (options.remote_prop === undefined) {
        remote_prop = util.downFirst(local.name);
        if (use_list) remote_prop = remote_prop+'s';
    } else remote_prop = options.remote_prop;

    let merge_options = {};
    
    if (debug) {
        console.log(`Relationship from ${local.name} to ${remote.name} local_prop: ${local_prop} remote_prop: ${remote_prop}`);
        merge_options.debug_name = `Relationship (${local.name}->${remote.name})`;
    }

    let missing_remote_promises;
    let missing_remote_resolvers;
    if (missing_node) {
        missing_remote_promises =new Map();
        missing_remote_resolvers = new Map();
    }
    
    if (!use_list) {
        merge_options.empty_node = () => {return {}};
        merge_options.add_item = (n,o) => n.value = o;
        merge_options.delete_item = (n,o) => n.value = undefined;
        merge_options.get_item = (n) => n.value;
    }
    
    function key(local_obj) {
        let ko = {};
        for (let i = 0; i < keys.length; i++) {
            if (local_obj[keys[i]] == undefined) // or null
                return undefined;
            ko[remote.syncPrimaryKeys[i]] = local_obj[keys[i]];
        }
        return remote.storageKey(ko);
    }

    const filter = mapFilter({
        ...merge_options,
        target: local,
        filter: key,
        collection: local.syncStorageMap,
                ...options,
    });
    

    Object.defineProperty(
        local.prototype, local_prop,
        {enumerable: true,
         configurable: true,
         get: function() {
             let local_key = key(this);
             if (debug)
                 console.log(`get ${local.name} key: ${local_key}`);
             let res =  remote.syncStorageMap.get(local_key);
             if ((res !== undefined) || !missing_node) return res;
             res = missing_node(local_key, this);
             let promise = missing_remote_promises.get(local_key);
             if (promise === undefined) {
                 promise = new Promise((resolve) => {
                     missing_remote_resolvers.set(local_key, resolve);
                 });
                 missing_remote_promises.set(local_key, promise);
             }
             res.loadedPromise = promise;
             return res;
         },
         set: function(v) {
             if (!(v instanceof remote))
                 throw new TypeError(`${local_prop} must be a ${remote.name}`);
             let new_key = remote.storageKey(v);
             if (new_key === undefined)
                 return;
             for (let i =0; i < keys.length; i++)
                 this[keys[i]] = v[remote.syncPrimaryKeys[i]];
             return v;
         },
        });
    Object.defineProperty(
        remote.prototype, remote_prop,
        {configurable: true,
         enumerable: true,
         get: function() {
             let res = filter.get(remote.storageKey(this));
             return res;
         },
        });

    if (missing_node) {
        function checkNodeFound(obj) {
            let key = remote.storageKey(obj);
            let resolver = missing_remote_resolvers.get(key);
            if (resolver) {
                resolver(obj);
                missing_remote_resolvers.delete(key);
                missing_remote_promises.delete(key);
            }
        }
        for (let o of filter.add_ops)
            remote.addEventListener(o, checkNodeFound);
    }
    
}

module.exports = {
    FilterBase,
    filter,
    mapFilter,
    relationship
};
