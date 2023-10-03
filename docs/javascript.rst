.. highlight:: javascript

.. default-domain:: js

Entanglement Javascript Support
===============================

Entanglement has a Javascript interface that allows websocket clients to connect to a Python server and provides two-way synchronization of objects.

* The :ref:`javascript schema generator <javascript:schema>` is used to export the Python schema to Javascript.

* This schema is imported into a javascript project typically built with Webpack.

* The schema is attached to a :class:`SyncRegistry`
* Persistence is set up.
* :ref:`filters <javascript:filters>` and relations are used to be notified of synchronized objects.

.. _javascript:schema:

Javascript Schema Generator
***************************

The set of primary keys and attributes associated with :py:class:`~entanglement.Synchronizable` classes is exported from the Python code to Javascript.
If a class has no special behavior in Javascript, then Entanglement will automatically generate a Javascript class to represent the *Synchronizable*.  In typical usage, :ref:`javascript classes <javascript:registering>` are required to customize the behavior of the synchronized object.  The automatically generated class can serve as a base class to set up basic properties and primary keys.  Unfortunately, because of a :ref:`bug <javascript:persist_bug>`, the automatically generated base classes will not work for persistent objects unless a custom class extending PersistentSynchronizable is created for each synchronized class.


First, in your Python code, attach a :py:class:`SyncRegistry` to a  javascript schema.

.. code-block:: python

    from entanglement.javascript_schema import javascript_registry
    from entanglement import SyncRegistry, Synchronizable

    registry = SyncRegistry()

    class SomeSync(Synchronizable):

        sync_registry = registry

        # some code

    javascript_registry(registry, "our_registry.js")

.. py:module:: entanglement.javascript_schema

.. py:function:: javascript_registry(registry:SyncRegistry, schema:str)

    :param registry: the :py:class:`~entanglement.SyncRegistry` that should be made available in javascript

    :param schema: The filename to write the javascript schema to.

Then, run the schema generator:

.. code-block:: none

    python3 -m entanglement.javascript_schema packages_containaing_registries

For example if ``project.entanglement.schema``  contains several sync registries, you might run:

.. code-block:: none

    python3 -mentanglement.javascript_schema project.entanglement.schema

That would produce schema for any call to :py:func:`javascript_registry` in the ``project.entanglement.schema`` package.  Generally, Entanglement Javascript projects also require the ``sql_meta.js`` schema produced by ``entanglement.sql.internal``.

Using a Schema
**************

Assume that entanglement schemas are stored in ``./entanglement_schemas``..  There is one schema called ``project.js`` and the ``sql_meta.js`` schema.
A project using Entanglement might look something like::

    import {SyncManager, SyncRegistry} from "entanglement";
    import {SyncOwner, relationship, setupPersistence} from "entanglement/persistence";
    import {filter, FilterBase} from "entanglement/filter";
    import MetaSchema from "../entanglement_schemas/sql_meta";
    import Schema from "../entanglement_schemas/project"

Each schema exports a default function called register_schema:

.. function:: register_schema(registry)

    :param registry: The :class:`SyncRegistry` to attach this schema to.

    Typical usage is to import the function and call it on a registry::

        import schema from "./entanglement_schemas/schema"
        const registry = new SyncRegistry({base:PersistentSynchronizable})
        schema(registry)

So, going back to our sample project, we would register the two schemas::

    registry = new SyncRegistry({base: PersistentSynchronizable})
    MetaSchema(registry)
    Schema(registry)

And then set up persistence::

    setupPersistence(registry)

.. function:: setupPersistence(registry)

    :param registry: a :class:`SyncRegistry` on which to enable persistence

    After the ``sql_meta`` schema is attached to a registry, *setupPersistence* must be called to attach javascript implementations to classes defined in the schema.

    
.. _javascript:registering:

Registering Custom Classes
**************************

To define custom behavior, define a Javascript class with the same name as the Python class.  Ideally this class extends :class:`PersistentSynchronizable`::

    class SomeSynchronizable extends PersistentSynchronizable {

        // Extra methods can go here

        }

    // and register the class
    registry.register(SomeSynchronizable)

If a class is :meth:`registered <SyncRegistry.register>` that is  in a schema attached to the registry, then:

* Attributes are made available for all sync attributes.

* Primary keys are handled.

.. _javascript:persist_bug:

.. warning::

    There is support for schema classes that are auto generated rather than calling register explicitly.  For this support to work in an application that uses :class:`PersistentSynchronizable`, the base class that is used to generate the auto-generated classes needs to be set when constructing the :class:`SyncRegestry` as is done in the examples above.

    Also, there is support for using an application specific base hierarchy so classes do not need to extend *Synchronizable*.  This works fine for applications that only use the interfaces of *Synchronizable*.  Applications using *PersistentSynchronizable* must supply classes that extend *PersistentSynchronizable* or set the base class when constructing the registry and use the automatically generated classes.  If a class does not extend *PersistentSynchronizable*, then only the methods of *Synchronizable* will be included.  As a result, each time an instance is synchronized, a new instance will be generated.  All the :ref:`filters <javascript:filters>` will fail.  Also methods required for updating, creating and deleting objects will not be present.

.. _javascript:filters

Filters
*******

Filters provide a way to be notified about changes, additions or removals of :class:`PersistentSynchronisables <PersistentSynchronizable>` matching certain criteria.  For example foreign key :func:`relationships <relationship>` are implemented using filters.

.. class:: FilterBase(options)

    A *FilterBase* is a generic categorized filter.  It takes a filter function that maps objects into categories.  In simple usage, the filter function can return true for objects that the filter wants to track or false/undefined for objects that should be ignored.  But for example a foreign key relationship could categorize child objects based on which parent they belong to.

    The *FilterBase*  calls an add function for the new category when an object is added to a category, and the remove function for the old category.  Internally the category is not used for anything else.  For example :func:`filter` implements a single-category filter that stores interesting objects in a list.  Its add function adds objects to the list; the remove function removes them from a list.

    *FilterBase*'s constructor takes an object containing the following possible options:

    :param target: The :class:`PersistentSynchronizable` that this *FilterBase* tracks.  Event listeners will be added to the target to track new and changed objects.

    :param filter: :code:`function filter(obj) {}` Returns the category for a given object.  If ``undefined``/``false`` is returned, the object is ignored.

    :param add: :code:`function add(obj, category) {}` A function called when an object is added to a category.  Objects are never added to the undefined category; when *filter* returns undefined, objects are ignored.

    :param remove: :code:`function remove(obj, category) { }` Called when an object is removed from a category.

    :param include_transitions:  If true, then the filter tracks Entanglement transition operations.

    :param debug: If true, log debugging state to console


    .. method:: close()

        Shuts down the filter and removes event listeners from the *target*.

    Events
    ______
    
    .. method:: onObjectChange(obj)

        Called whenever an object changes, even if it does not change categories.  

    .. method:: onAdd(obj, category)

        Called when an object is added to a category.

    .. method:: onRemove(obj, category)

        Called when an object is removed from a category.

    .. method:: onChange(obj, old_category, new_category)

        Called when an object's category changes.
        

.. function:: filter(options)

    :returns: A :class:`FilterBase` that stores objects matching the filter function in a list.  The returned *FilterBase* will have a *result* property which is a list containing the current objects.

    :param filter: A filter function returning true for objects that should be tracked.  It is important this function always return the same value for objects that are interesting.

    :param target: The :class:`PersistentSynchronizable` that this filter applies to.

    :param order: :code:`function(a,b) {}` A function that is a sort comparitor.  If supplied, whenever the result element is accessed, it will be sorted if needed.

    Other options are also passed through to :class:`FilterBase`, but *add* and *remove* should not be set.

    .. note::

        For Vue3 it's probably desirable that the eventual result list be something that can be passed in as an option so that a reactive list can be used when desired.  For vue2 it is sufficient to call $vue.observer on the result, since that mutates the underlying object.

.. function:: mapFilter(options)

    Like *filter* except for maps instead of lists.  It's kind of complicated and we'll document later.  The map key is the category; the map value can either be a list or a singleton depending on configuration.

.. function:: relationship(local, remote, options)

    Sets up a foreign key relationship between two :class:`PersistentSynchronizable` classes.  Even supports promises when a child is received before a parent.

    :param local: The child side of the relationship (the one with the foreign key constraint in a database)

    :param remote: The :class:`PersistentSynchronizable` class that is the parent side of the relationship.
    :param options: Options to configure the relationship:

        keys
            A list of attribute names corresponding to ``remote.syncPrimaryKeys``.  Must be the same length as ``syncPrimaryKeys``.

        use_list
            If true, then *remote_prop* is a list; there can be many *local* objects for each *remote* object.  If false, then this is a one-to-one relationship.

        local_prop
            The name of the property on *local* that will be added to refer to the parent remote object.

        remote_prop
            The name of the property to be added to *remote*.  If *use_list* is true, this contains a list of *local* objects; otherwise it contains a single *local* object.

            If not specified, the default name is *local.name* with the first letter downcased; if *use_list* is true, a ``s`` is added to the name.

        missing_node
            :code:`function(key, local_obj){}`  A function returning the value of *local_prop* when no *remote* is found with key *key*.  This can be used for example to create a proxy UI object when some objects are still being loaded.  This could even be used to subscribe to a remote object outside of the current view.  Whatever is returned, the *loadedPromise* property will be set to a promise that will be resolved if a *remote* with key *key* is ever synchronized.  If this option is not set, *local_prop* will be ``undefined`` and missing nodes will not be tracked.

                  


            

    
Javascript API
**************

.. class:: SyncRegistry(options)

    Represents a javascript version of :py:class:`SyncRegistry`.
    The constructor takes a dictionary of options:

    :param base:  When auto-generating classes that are in the schema but for which :meth:`register()` has not been called, which base class should be extended.  Typically either *PersistentSynchronizable* or *Synchronizable*.  By default *Synchronizable*.

    .. method:: register(class)

        Add *class* to this registry.  *class* must either be a *Synchronizable* or a class with the same name must be in a schema attached to the registry.  If a schema containing the class is attached, then attributes and primary keys will be added to *class* when the registry is first attached to a :class:`SyncManager

    .. attribute:: registry

        A :class:`Map`.  Keys are the class names and values are the classes (either auto-generated or manually supplied).
        

.. class:: PersistentSynchronizable()

    .. method:: set syncStorageMap()

        Sets the map in which objects of this class are stored.  By default classes generate their own, but it may be desirable to have all classes below a certain point in the inheritance tree stored in the same map.  If this is done, then foreign key constraints can refer to any class implementing the right interface.

    .. method:: syncModified()

        :returns: A set of attributes modified since the object was received.

    .. method:: syncUpdate(manager)

        Send a *forward* of this object toward its owner using *manager*.  I.E. Save local  modifications.

        :returns: A promise that resolves to the result of the forward operation.  Assuming :meth:`syncConstruct` is working typically, on success, the promise will resolve to *this*.  On failure the promise resolves to some *SyncError*.


    .. method:: syncCreate(manager)

        Creates this object on *manager*.  *_sync_owner* must already be set.

        :returns: A promise that resolves to the created object.  Unlike for updates, that object is not typically *this*.

    .. method:: syncDelete(manager)

        Synchronizes a delete of *this* to *manager*.
        
